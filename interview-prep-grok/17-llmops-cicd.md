# Module 17: LLMOps & CI/CD for AI

**Study + interview prep.** Grounded in research dated 2026-09-03 (62 sources). This file is the **promotion control plane**: git + CI + registry + feature flags — **how prompts, models, schemas, and evals ship**. It is **not** Agent Server internals ([13-deep-agents-production.md](13-deep-agents-production.md): checkpointer, SSE, queue workers — cited as **one deploy target** / revision). It is **not** the prompt compiler ([16-prompt-engineering.md](16-prompt-engineering.md): MIPROv2/GEPA, few-shot/CoT — cited for **why** a prompt is a hashed artifact; DSPy `compile` is an **offline job this module promotes**). Judge math, Miller power, majority-of-3, flake-vs-drop live in [04-evals.md](04-evals.md) — **cited**; this file is **when the gate blocks merge/promote**. Traces as online monitors: [05-observability.md](05-observability.md). Cache bust on promote: one paragraph citing [03-caching.md](03-caching.md). Embedding `model_id` / `index_gen` without rebuild: [15-embeddings-vector-databases.md](15-embeddings-vector-databases.md). Pin the invariant: a **release** is `(model_id, prompt_hash, schema_hash, decoding_params, index_gen, eval_suite_id)`. Changing any field is a new release. Env tags (`production`, `staging`, `@champion`) are **mutable pointers**, not the artifact. Serving `latest` / unpinned `main` is **not** a release. `$ per 1k` is **[inferred]** from published rates × the stated loop, not a vendor SKU. Public pages do **not** publish a p50/p95/p99 for “an eval job” or a global control-plane SLO — missing percentiles are architecture-derived **[inferred] policy in ms**, two clocks (user vs CI). OpenAI hosted Evals shut down **2026-11-30**. Bedrock Provisioned Throughput is **capacity**, not a registry. MLflow stages are deprecated → aliases `@champion`. Zero-Trust MCP (`promote_tag`, `registry.set_alias`, `eval.run`) is **§4.4 in this file**; do not defer to 19. 16 owns authoring tools (`prompts/get`, `prompts/set`, `compile_program`); this file is the **promotion PEP**.

---

## What Is This?

**LLMOps is not “we use MLflow.”** It is a **release train**: git authors the bundle, CI is a fail-closed gate, a registry stores an **immutable version**, an alias/tag is a **mutable pointer**, and a feature flag is the **blast-radius dial**. Live inference (08/13) never waits for the CI judge. Online eval is a **sidecar** (04/05). Agent Server / LangSmith Deployment is **one kitchen**; the recipe still came from git.

Two independently scaled planes share a **pinned bundle + a mutable alias**:

| Plane | Owns | Failure if coupled |
| --- | --- | --- |
| **Control (promotion)** | git (prompt/schema/eval YAML), CI (golden-set gate, flake retries), model/prompt registry (immutable version + mutable alias), feature-flag assignment (sticky hash), eval snapshots, who-can-promote | Playground save becomes prod; A/B assignment flips mid-thread; rollback waits on a 20-minute Agent Server rebuild |
| **Data (live inference)** | Unchanged token path from 08/13: prefill + decode + tools. Does **not** wait for the CI judge. Online eval is a sidecar | Judge on the request thread; canary traffic shares the eval-job TPM pool |

Think of a **container registry**, not a chat playground. **Control** is digest vs tag: which SHA is `@champion`, who may `set_alias`, which golden `eval_suite_id` cleared the gate, which flag variation is 5%. **Data** is the running replica: resolve flag → bundle → model API; judge **off** user p99. **Persistence** is the artifact store, experiment DB, eval snapshots, Hub commits — not the request. **Tool proxies** are MLflow/Hub/SageMaker/Vertex APIs and MCP **promote** tools behind a gateway — not a laptop with the prod Hub token. **Telemetry** is experiment id, canary task-metric, alias audit. If you merge the playground into the serving path, a save is an incident, and `latest` is what you shipped.

**Interview one-liner:** I ship a **bundle**, not a prompt string. CI is fail-closed on a versioned golden suite; online eval is fail-open with coverage%. Aliases are pointers; hashes are the release. Bedrock PT is capacity. I will not A/B 50 items and call 3 pp.

## Why It Matters

Every copilot, extraction API, and agent graph that iterates weekly is this layer. Interviews test whether you split **control vs data**, refuse serving `latest`, put **flags in front of the graph** (13 hosts the graph; this module selects the bundle), budget CI as **$0.88–$21.13 / 1k tasks** Luna vs Sonnet-m3 **[inferred]**, treat Miller **n ≈ 969** as the **promote N** not a vibe check (04), and put **Zero-Trust MCP on `promote_tag` / `registry.set_alias` / `eval.run` in this file**.

The cost trap is not “evals are cheap.” It is **Sonnet majority-of-3 as the default ship judge** (**$21.13 / 1k** vs Luna-m3 **$2.32 / 1k** **[inferred]**) and **100% shadow forever** (**+$880/mo** on a 1M Luna baseline). The latency trap is putting the judge on the user clock (16 extraction **p50 1,200 / p95 4,000 / p99 12,000 ms [inferred]** is the inference SLO this module **must not inflate**). The security trap is an assistant MCP tool that can `set_alias` production, or a golden set with PAN that LangSmith retains **indefinitely**.

---

### 1. System Topology & Data Flow

Five planes, **not** a single “push to prod.” Git/CI/registry/flags are control; live inference is data (judge **OFF** user p99); artifact store / experiment DB / eval snapshots / Hub commits are persistence; vendor registry APIs + MCP promote tools are proxies behind a gateway; experiment id / canary metrics / alias audit are telemetry.

```
                         TELEMETRY / OBSERVABILITY SINKS
         ┌──────────────────────────────────────────────────────────────────┐
         │  eval experiment_id  (suite_id @ as_of; pin VERSION not @latest) │
         │  canary vs control: task metric, parse_ok, 5xx — not vibe 1–5    │
         │  alias audit: who, prev_hash, new_hash, ticket_id                │
         │  coverage% of online sidecar (05); spend-cap ≠ quality green     │
         │  WORM: (ts, actor, promote|rollback, release tuple, experiment)  │
         │  flag assignment: salt, pct, thread_id stickiness                │
         └────────────▲─────────────────────▲──────────────────▲────────────┘
                      │ spans               │ meters            │ audit
                      │                     │                   │
┌─────────────────────┴─────────────────────┴───────────────────┴───────────┐
│ CONTROL PLANE  (promotion — LLM-free at request admit; judges run in CI)  │
│                                                                           │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌─────────┐ │
│  │ git SoT    │ │ CI golden  │ │ registry   │ │ alias/tag  │ │ flags   │ │
│  │ prompt +   │ │ fail-closed│ │ immutable  │ │ staging /  │ │ 5%      │ │
│  │ schema +   │ │ miller N   │ │ version    │ │ @champion  │ │ canary  │ │
│  │ eval YAML  │ │ m3 on ship │ │ SHA/ver    │ │ pointer    │ │ kill=0  │ │
│  └─────┬──────┘ └─────┬──────┘ └─────┬──────┘ └─────┬──────┘ └────┬────┘ │
│        │              │              │              │              │      │
│        ▼              ▼              ▼              ▼              ▼      │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │ PR → gate → register → staging alias → 5% canary → promote         │  │
│  │ Pin: model_id, prompt_hash, schema_hash, decoding_params,          │  │
│  │      index_gen, eval_suite_id. Tag production is a POINTER.        │  │
│  └────────────────────────────────────────────────────────────────────┘  │
└────────────────────────────────┬──────────────────────────────────────────┘
                                 │ pinned bundle + alias / flag variation
                                 ▼
┌───────────────────────────────────────────────────────────────────────────┐
│ DATA PLANE  (live inference — judge OFF user p99; 08/13 token path)       │
│                                                                           │
│  flag SDK (local) → sticky thread_id → resolved bundle → model_id         │
│                   → prompt_hash in prefix → schema_hash decoder           │
│                   → index_gen retrieve (15)                               │
│                                                                           │
│  ┌────────────── TOOL PROXIES (least privilege — not a prod Hub token) ─┐ │
│  │ MLflow / UC / W&B / Hub / SageMaker / Vertex / GitLab registry APIs  │ │
│  │ MCP behind gateway PEP:  eval.run  |  registry.set_alias             │ │
│  │                         | promote_tag                                │ │
│  │   eval.run: staging suite only for assistants                        │ │
│  │   set_alias / promote_tag: human + break-glass — NEVER the model     │ │
│  │ Identity = verified token / RunContext. MCP is not the registry PEP  │ │
│  │ No token passthrough to Hub / MLflow / SageMaker                     │ │
│  └──────────────────────────────────────────────────────────────────────┘ │
│  Online judge = sidecar sampler (04/05). Spend cap pauses evaluator.      │
└─────────┬───────────────┬─────────────────┬─────────────────┬─────────────┘
          │               │                 │                 │
          ▼               ▼                 ▼                 ▼
┌───────────────────────────────────────────────────────────────────────────┐
│ PERSISTENCE LAYER  (commits ≠ serving path; rollback = retag, not rewrite)│
│                                                                           │
│  ┌──────────────┐ ┌──────────────┐ ┌─────────────┐ ┌──────────────────┐  │
│  │ Artifact     │ │ Experiment   │ │ Eval        │ │ Hub / git        │  │
│  │ store:       │ │ DB: runs,    │ │ snapshots:  │ │ commits: prompt  │  │
│  │ weights,     │ │ traces,      │ │ suite_id @  │ │ SHA; git is SoT  │  │
│  │ LoRA, URI    │ │ params       │ │ as_of       │ │ for audit        │  │
│  └──────────────┘ └──────────────┘ └─────────────┘ └──────────────────┘ │
│  Alias `production` is mutable. Serving reads alias → logs the tuple.     │
│  GC retains last N production versions. Never delete what prod still names│
└───────────────────────────────────────────────────────────────────────────┘
```

**Planes (do not couple):**

| Plane | Lives here | LLM-free? | Failure if coupled |
| --- | --- | --- | --- |
| **Control** | git, CI gate, register, alias, flags, MCP allowlist | Yes at admit. Judges run **offline** in CI | Playground string in prod; judge on user p99 |
| **Data** | Flag resolve, inference, tools | No — untrusted stream | Letting the model pick `alias` or skip the gate |
| **Persistence** | Artifacts, experiments, snapshots, Hub commits | Yes | Treating `latest` as a replay key |
| **Tool proxies** | Registry SDKs + MCP promote tools behind gateway | Yes for authz | Omnibus `set_alias(production)` from an assistant |
| **Telemetry** | experiment_id, canary metric, alias WORM | Yes | Logging golden PII / prompt bodies “for debug” |

**Canonical 7-step path (how a change becomes a release):**

1. **Author** in git: prompt template, JSON Schema / GBNF, decoding params, eval suite pointer. Hash = `prompt_hash` / `schema_hash` ([16-prompt-engineering.md](16-prompt-engineering.md)). DSPy `compile` if used is an **offline job** whose output JSON is the artifact — this module only **promotes** it.
2. **CI golden-set gate** (fail-closed): pin `eval_suite_id` + dataset `as_of`. Majority-of-3 judges on **ship gates only** ([04-evals.md](04-evals.md)). Non-zero exit **blocks merge**.
3. **Register:** immutable version in MLflow / W&B / Hub / SageMaker / Vertex / GitLab. Do **not** retag `production` yet.
4. **Stage alias:** `staging` / `@challenger` / SageMaker `PendingManualApproval`. Preview deploy (LangSmith Deployment **revision** is one target — §1.8).
5. **Canary / shadow** via **flags**, not by rewriting the serving binary. Sticky assignment on `user_id` / `thread_id` + experiment salt.
6. **Promote:** move `production` tag / `@champion` / `Approved` + flag 100%. Rollback = **retag previous hash**, not rewrite the blob. **Flag first.**
7. **Observe:** online evals + traces ([05-observability.md](05-observability.md)). Kill switch is the **flag**, not a 20-minute image rebuild.

**Request-flow narrative (PR → golden gate → register → staging alias → 5% canary → promote):**

1. **PR / webhook.** Author changes prompt/schema/eval YAML (or Hub webhook if PMs edit Hub — still require CI green). CODEOWNERS + required check.
2. **Gate.** Unit/integration + offline eval on pinned suite. PR smoke `--first N`. Merge: full golden; majority-of-3 if the metric is LLM-judge (04). Fail-closed on **metric drop** and on exceptions. Braintrust default reporter fails on **exceptions only** — custom `Reporter.reportRun` required to block on quality.
3. **Register.** Immutable version. Alias still `staging` / unset. Release record stores the six-tuple + experiment id.
4. **Stage.** Point `staging` / `@challenger` at the new version. Optional preview Deployment revision (13). Graph/code change → new revision; **prompt-only → flag**, do not rebuild Agent Server to change a tag.
5. **Canary.** Flag serves candidate to p% (often 5%) sticky on `thread_id`. Same SKU → token delta ≈ **$0**. Kill = p=0 within flag TTL.
6. **Promote.** CAS: `If-Match` previous hash. Move `production` / `@champion`. Flag 100%. Bust app cache key that includes hashes (03, §3.4).
7. **Observe / stop.** Sidecar online eval, fail-open, coverage%. Canary kill on hard-oracle / error-rate, not a single noisy judge tick.

**Interview traps in this diagram:**

- Agent Server is **not** the registry. LangSmith Deployment revision is a **CD contract** (poll `DEPLOYED`); flags sit **in front of** the graph.
- Bedrock PT / Reserved = **capacity** for a `model_id` you already chose. Interview fail: “we registered the model on Bedrock.”
- Vertex deleting the `default` version **reassigns default to the next most recent** — not a hard pin.
- Judge on the user thread inflates 16’s extraction SLO. Two clocks (§3.5).

**LangGraph / LangSmith Deployment as one target (not a recopy of 13).** LangSmith splits control plane (org, deployments, revisions, billing) from data plane (Agent Servers + Postgres + Redis) — internals in 13. This module’s CD contract: `POST /v2/deployments/{id}/revisions` always creates a revision; poll until `DEPLOYED`. Cloud: GitHub integration or API. Hybrid: `langgraph build` → image URI. CLI: `langgraph deploy`. Policy: PR → **preview** deployment; merge → delete preview, **production** revision. PromptHub commit is a first-class trigger alongside code push. Other legitimate targets: SageMaker endpoint from Approved package, Vertex endpoint from `@production`, HF Inference Endpoint at a **full SHA**, self-hosted vLLM with a git SHA, Bedrock on-demand / reserved / PT for the **model_id already in the bundle**.

---

### 2. Core Mechanics & Algorithms

#### 2.1 Invariants (promotion control plane)

**I1.** Release pin is six-tuple: `model_id + prompt_hash + schema_hash + decoding_params + index_gen + eval_suite_id`. Any change → new release. Env tag is a **pointer**. Serving `latest` is not a release.

**I2.** CI is **fail-closed** on the versioned golden suite. Online eval is **fail-open** with coverage% (04/05). A red CI with green prod is **correct**. Never skip the merge gate because the judge API 429’d.

**I3.** Traffic split is a **control-plane** decision. Data plane sees only a **resolved bundle**. Sticky hash on `thread_id`; dual hashes during canary; do not switch mid-conversation (13 checkpointer must not assume a single system prompt across resume).

**I4.** Rollback RTO is **flag 0%** (seconds), then alias retag (MLflow alias cache **60 s** = **60,000 ms**), then image rebuild (**~20 min**). Order is mandatory.

**I5.** Changing `index_gen` / embedding `model_id` without dual-running indexes is a **retrieval incident**, not a prompt hotfix ([15-embeddings-vector-databases.md](15-embeddings-vector-databases.md)). Changing `schema_hash` without the prompt in the same PR is a `parse_ok` incident (16).

**I6.** DSPy/MIPRO/GEPA internals are 16. This module promotes the **frozen** `program.json` like any other prompt artifact. Optimizer telemetry (`use_wandb`) is not the LLMOps platform.

#### 2.2 Model / prompt registries: immutable version + mutable pointer

Classical ML registered a `.pkl` / MLmodel directory. LLMOps still uses the same **registry object**, but the bytes are often a **pointer** (provider `model_id`, LoRA adapter, prompt URI) plus metadata. Stages that implied a single Production version are replaced by **aliases** so two versions can run (champion / challenger).

| System | Immutable unit | Promotion primitive | Notes |
| --- | --- | --- | --- |
| **MLflow Model Registry (OSS)** | Registered model **version** (integer), linked to a Tracking **run** | Legacy **stages** None / Staging / Production / Archived — **deprecated** since ~2.9; migrate to aliases | URI `models:/name/Production` → `models:/name@champion`. Multiple aliases per version; each alias → **one** version. A/B: `@champion` + `@challenger` |
| **Databricks Unity Catalog** | `catalog.schema.model` version. Stages **unsupported** | **Aliases** (`Champion` / `Challenger` / free string) + three-level **environment** (dev/staging/prod catalogs) (docs updated **2026-06-23**) | `prod` catalog ≠ serving prod traffic. Copy: `copy_model_version` then `set_registered_model_alias`. Privileges: `CREATE MODEL` / `CREATE MODEL VERSION` / `EXECUTE`. MLflow 3 default URI `databricks-uc`. New UC versions **require a signature**. `get_latest_versions` unsupported on UC |
| **W&B Registry** | Artifact **version** `v0, v1, …` logged on a **run**, then **linked** (pointer, not copy) into a **collection** | **Aliases** unique per collection (`latest`, `production`). **Protected aliases** (lock icon): only registry Admin can add/remove. **Tags** are many-to-many labels, not pointers | `run.link_artifact(..., aliases=["production"])`. Automations/webhooks on alias add = CD |
| **Hugging Face Hub** | Git **commit SHA** (full **40-char**). Repo is git + **Xet** (or legacy LFS) | **revision** = branch / tag / SHA. Tags are git tags (moveable unless you treat SHA as SoT). No Staging/Production enum | `hf_hub_download(..., revision="<full sha>")`. Short 7-char hashes are not the production pin. `main` moves. Inference Endpoints take `revision` at create/update. Model card = documentation, **not** a gate |
| **SageMaker Model Registry** | **Model package** version inside a **ModelPackageGroup** | **ModelApprovalStatus:** `PendingManualApproval` → `Approved` / `Rejected`. EventBridge `SageMaker Model Package State Change` can auto-approve from metrics | Deploy only when Approved. IAM is the PEP. Not prompt-native |
| **Vertex AI Model Registry** | Model resource + numeric **version_id**. First version gets alias **`default`**; exactly one default | User aliases (`staging`, `production`, `stable`, `candidate`). `models.mergeVersionAliases`; prefix `-` removes (`-golden`) | `Model('.../models/{id}@{alias}')`. **Footgun:** deleting the default version **reassigns default to the next most recent** |
| **Amazon Bedrock Provisioned Throughput** | **Not a registry.** Capacity SKU: Model Units billed hourly; required for **customized** models. 2026 **Reserved** tier = TPM commitment, 1- or 3-month, overflow to Standard, **99.5%** uptime **target** | You **purchase throughput** for a model ID you already chose. No Staging/Production alias | Interview trap. Versioning still lives in git / SageMaker / prompt hub. Flex (−50% vs Standard) is the documented home for **evals and summarization**, not user p99 |
| **GitLab Model Registry** | Semver model versions, MLflow-client compatible; artifacts via Package Registry (**5 GB**/file) | Link version → CI job. Reporter+ to modify/delete | Blueprint: import from Vertex/HF is a stated direction — treat native HF import as **not generally available** unless the issue is closed |

**MLflow Prompt Registry:** `register_prompt` / `load_prompt`; versions immutable; aliases `@production` / `@latest`; **version cache TTL infinite**; **alias cache TTL 60 s** (`MLFLOW_ALIAS_PROMPT_CACHE_TTL_SECONDS`). Cache invalidates on `set_prompt_alias` / `delete_prompt_alias`. `model_config` (temperature, max_tokens) can sit **on the prompt version** — that is `decoding_params` in the release tuple. `response_format` is the schema hook; still hash it in git as `schema_hash` (16) so a Hub-only JSON schema cannot drift.

> ⚠️ Gap: Databricks Unity Catalog has **no public $ per registered model version**. Do not invent a UC SKU.

#### 2.3 What is a “run” when the artifact is a prompt hash?

A classical MLflow **run** wraps `start_run()`: params, metrics, artifacts (often a serialized model). A GenAI “run” is **not** that object. Vendors overloaded the word.

| Vendor | Container | Atomic execution | What you pin for a prompt change |
| --- | --- | --- | --- |
| **MLflow Tracking** | **Experiment** (one per app recommended for traces) | **Trace** (OTel-compatible tree) and/or a Tracking **run** if you still `start_run` during eval | `mlflow.genai.register_prompt` → immutable **prompt version** + optional `model_config`. `evaluate(...)` logs lineage prompt-version ↔ eval. Load `prompts:/name/1` (immutable) vs `prompts:/name@production` (pointer) |
| **W&B Models** | **Project** / **run** | `wandb.init()` run logs metrics + Artifacts | Artifact of type `prompt` + `link_artifact` into Registry. Lineage: dataset → run → artifact → collection |
| **W&B Weave** | Weave project (`weave.init`) | **Call** / **trace** on `@weave.op`. **`Evaluation` is a blueprint**; **`.evaluate()` is a run** | Dataset + scorers + model/op. `get_evaluate_calls()` returns **many** calls per Evaluation object (re-runs). Pin Weave object refs, not “the latest eval in the UI” |
| **LangSmith** | **Dataset** of **examples** (inputs + optional reference outputs) | Offline: **experiment** = one application version × one dataset; each example produces a **run** (trace) with `reference_example_id`. Online: **run** = production trace, **no** reference output. REST: experiment = tracer **session** with `reference_dataset_id` | Prompt **commit hash** (immutable) vs **commit tag** (pointer). Experiment metadata via `LANGSMITH_EXPERIMENT_METADATA` in CI |
| **Braintrust** | **Project** / **experiment** | `Eval` / `bt eval`: one experiment per invocation; rows scored; `Reporter.reportRun` → process exit | Code-defined eval files in git. `--first N` / `--sample N` = **non-final** smoke; full run on merge |

**Interview one-liner:** a **run** is one execution of a pinned bundle on one example (offline) or one live request (online). The **experiment** is the matrix. The **release** is the alias move after the experiment clears the gate. Do not log a prompt edit as a metric on yesterday’s `.pkl` run.

#### 2.4 Prompt versioning: Hub commit vs tag vs git SoT

**LangSmith Hub — commit vs tag:**

- Every save creates an immutable **commit** with a **commit hash**. Pull: `client.pull_prompt("name:commit_hash")`.
- **Commit tags** are mutable labels; each tag → exactly one commit. `client.pull_prompt("joke-generator:production")`.
- Reserved tags **`staging`** and **`production`** are **environments**. They are **not** in the freeform tag picker. Promotion is a dedicated UI flow (and API `prompts:tag` / owners). Promoting to Production **does not** remove Staging. Rollback uses **per-environment ordered history**, not git revert of the blob.
- **Prompt owners:** default “workspace users with `prompts:tag`”; **Owners only** restricts who can tag, promote, delete. Creator is auto-owner. Removing yourself is irreversible without another owner.
- **Webhook:** **one per workspace**. Payload includes `prompt_id`, `prompt_name`, `commit_hash`, `created_by`, `manifest`. Playground commit can skip webhooks; API `skip_webhooks`. Use this to trigger CI when Hub is **not** git.
- Resource tags ≠ commit tags.

SDK: `push_prompt(..., commit_tags=..., parent_commit_hash="latest")`. Historical gap: tagging commits from CI required UI (`langsmith-sdk#2126`, comments into 2026-01); treat programmatic `commit_tags` as **version-check the SDK**, not a 2024 assumption.

> ⚠️ Gap: Hub **client** cache TTL is **not** a LangSmith SLO. Community reports ~**5 min** — **unverified**; measure your SDK. Do not budget rollback on a 5-minute rumor. Incident rollback is **flag first**.

**Git-as-source-of-truth vs hosted hub:**

| Pattern | Promote mechanic | When it wins | Failure |
| --- | --- | --- | --- |
| **Git SoT** | PR → CI eval → merge → CD sets Hub/MLflow alias to the commit’s hash | Audit, CODEOWNERS, `schema_hash` in the same diff as the prompt | Hub playground save bypasses git → **silent drift** |
| **Hub SoT** | Playground commit → webhook → CI → tag `production` | PMs iterate without PRs | CI must **pull the commit hash**, not `latest`; owners-only on tags |
| **Dual-write** | Git merge pushes Hub; Hub webhook no-ops if hash already in git | Migration | Two writers without hash equality checks |

**`schema_hash` pin (from 16, not recopied):** the decoder constraint is part of the release. CI must fail if the served schema digest ≠ git. A DSPy compile job that emits a new schema without a new `eval_suite_id` is an incomplete release.

**OpenAI reusable prompts (`v1/prompts`):** deprecation announced **2026-06-03**; shut down **2026-11-30** with hosted Evals. Do not design a 2027 control plane on OpenAI prompt objects. Migration: Promptfoo + prompts in application git.

#### 2.5 A/B, canary, shadow, flags

Traffic split is **control-plane**. The data plane should only see a resolved bundle.

- **Sticky assignment:** hash `experiment_salt + user_id` (or `thread_id` for multi-turn). Per-request coin flips mix two `prompt_hash` values in one conversation and contaminate session metrics. Change salt between experiments so the same 5% are not perennial guinea pigs.
- **Canary:** flag serves candidate bundle to p% (often 1–5%). Kill = set p=0; RTO is flag TTL, not image deploy. Bundle **model + prompt + decoding** in one variation (LaunchDarkly AgentControl: one config, many variations; SDK returns model+messages; **app** calls the provider — LD does not proxy tokens).
- **Shadow / dark launch:** candidate runs on sampled live inputs; user still gets control output. Cost ≈ **2× model $** on the sampled slice. Use for fine-tunes and embedding-model swaps where wrong answers must not ship. Score offline (nDCG, task metric) — 04/15.
- **Feature flags:** targeting rules, segments, percentage rollouts, experiments. AgentControl adds offline evals against golden sets and “drift triggers rollback” as **product claims** — treat auto-rollback thresholds as **your** metrics wired to the flag API, not a published SLO.

**Why LLM A/B needs more N than classic CTR — cite 04, do not recopy.** Task-unit power (Miller: **n ≈ 969** independent questions to detect **3 pp** at α=0.05, β=0.20, σ²≈1/9). A **50-item** golden set **cannot** support a 3 pp ship claim. Judge noise (position bias, verbosity, self-enhancement); T=0 still has SD >1.5 pp. Online, LLM output variance **stacks** on user-behavior variance. CUPED (Deng et al., WSDM 2013) cut Bing metric variance ~**50%** (half the users or half the duration) using pre-experiment covariates. CUPED needs correlated pre-period data — useless for brand-new users. 2026: one platform experiment, CUPED **15.5%** variance reduction; LLM-pred **1.3%** extra. Sequential / always-valid p-values (Johari et al., **mSPRT**) exist because **peeking** inflates Type I error. LLM A/B that “stops when p<0.05 in the dashboard” is invalid unless the platform uses always-valid sequences. **This file’s job:** the gate does not merge/promote on an underpowered canary; canary is a **blast-radius limiter**; power comes from golden N + CUPED online.

#### 2.6 Regression detection: golden CI vs online sidecar vs canary kill

| Gate | When | Metric class | Fail mode |
| --- | --- | --- | --- |
| **Golden-set CI** | PR / merge / pre-promote | Hard oracle first (schema, DB goal-state, tests). Soft rubric only after hard pass. Majority-of-3 on **CI ship** (04). Pin dataset **version/tag** (`as_of`) | Flake vs real drop: temp 0, structured judge, order-swap pairwise, retries on **idempotent** score calls — not on the user (04). Braintrust default reporter fails on **exceptions**, not on score drop — **custom `Reporter.reportRun`**. Promptfoo `fail-on-threshold` / jq on `stats.failures` |
| **Online eval monitors** | Production traces | Reference-free: safety, JSON schema, latency, sampled LLM-as-judge. Sampling rate (e.g. 0.1) ([05-observability.md](05-observability.md)). **Fail-open** + **coverage%**. LangSmith: scoring can **auto-upgrade** traces to extended retention | Spend cap pauses **evaluator**, not the agent (04). Do not treat coverage collapse as quality green |
| **Canary kill** | Flag p% | Same **task metric** as CI (nDCG@k, pass^k, tool-success), **not** “vibe” 1–5. Compare canary vs control on the **same** window; check covariate balance | Canary too small: underpowered (Miller). Kill on hard-oracle drop or error-rate; not on a single noisy judge tick |

**nDCG / task metric vs vibe:** retrieval/index changes use nDCG (15). Agent tool correctness uses hard oracles (04). Anthropic’s own eval guidance: “good” = F1 ≥ 0.85 on a **10,000**-example held-out set for a sentiment example, not a chat vibe.

#### 2.7 Pipeline DAG (GitHub Actions / GitLab CI)

Canonical DAG (LangSmith’s published example is the template, not the only target):

```
  PR / PromptHub webhook / online-eval alert
       │
       ▼
  ┌─────────────────────────────────────────┐
  │ unit + integration + e2e                │
  └──────────────────┬──────────────────────┘
                     ▼
  ┌─────────────────────────────────────────┐
  │ offline eval                            │
  │ OpenEvals / AgentEvals / pytest-        │
  │ langsmith / bt eval / promptfoo         │
  │   PR: --first N / cassette cache        │
  │   merge: full suite + m3 if LLM-judge   │
  └──────────────────┬──────────────────────┘
                     ▼
  ┌─────────────────────────────────────────┐
  │ langgraph /ok probe  30,000 ms then fail│  ← graph **health**, not eval SLO
  └──────────────────┬──────────────────────┘
                     ▼
  ┌─────────────────────────────────────────┐
  │ registry: register version, alias=staging│
  └──────────────────┬──────────────────────┘
                     ▼
  ┌─────────────────────────────────────────┐
  │ preview deploy (optional revision)      │
  └──────────────────┬──────────────────────┘
                     ▼
  ┌─────────────────────────────────────────┐
  │ canary flag 5%  (sticky thread_id)      │
  └──────────────────┬──────────────────────┘
                     ▼
  ┌─────────────────────────────────────────┐
  │ promote alias=production + flag 100%    │
  └─────────────────────────────────────────┘
```

**GitHub Actions patterns:** Braintrust `braintrustdata/eval-action@v2` (`runtime: node|python|go`, `permissions: pull-requests: write`, PR comment with scores). Other CI: `bt eval --no-input --json`. LangSmith: `@pytest.mark.langsmith` syncs tests → dataset + experiment; `LANGSMITH_TEST_CACHE` cassette cache to avoid re-paying LLMs. Official sample: eval job then Control Plane deploy. Promptfoo: `promptfoo/promptfoo-action@v1`; cache `~/.cache/promptfoo`; path filter `prompts/**`; `fail-on-threshold`. OpenAI’s post-Evals path. Secrets: `LANGSMITH_API_KEY`, `BRAINTRUST_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` as **repo secrets** — never in YAML.

**GitLab CI:** same DAG in `.gitlab-ci.yml` (`rules:`, `needs:`, `artifacts:`). Model registry link from the job via MLflow compatibility. Production **git tag** as the human-readable promote if you do not use Hub tags.

#### 2.8 Artifact bundle = the release

```
release = {
  model_id,          # provider ID or registry URI@alias resolved to version
  prompt_hash,       # git blob / Hub commit / MLflow prompt version
  schema_hash,       # JSON Schema / GBNF / response_format (16)
  decoding_params,   # temperature, top_p, max_tokens, seed, thinking budget
  index_gen,         # embedding model + index alias generation (15)
  eval_suite_id,     # dataset version + scorer versions + k
}
```

Serving reads **alias** → resolves to this tuple → logs the tuple on every trace (05). Rollback retags the alias to the previous tuple. Changing `index_gen` without dual-running indexes is a retrieval incident, not a “prompt hotfix.”

**Complexity (interview):** sticky assignment is O(1) hash. Alias resolve is a cache read (MLflow **60 s**). CI wall-clock is **throughput-bound** (TPM + m3 + flake), not O(n) Python. Compare-and-swap promote is one conditional write; lost CAS → retry with fresh etag, never “set production twice.”

---

### 3. Token Economics & NFR Analysis

All `$ per 1k` below are **[inferred]** = published rates × this section’s loop. Not a SKU. Not a vendor p50 for “an eval job.”

Reference loop (stated): task model **GPT-5.6 Luna** **$0.20 / $1.20 per 1M** in/out (same 2026-09-03 index rates used in 16). Judge optional **Claude Sonnet 4.6** **$3 / $15 per 1M**. Shape: **2,000 in / 400 out** per task; judge **1,500 in / 150 out** per sample.

Formula: `1k × (in_tok/1e6 × $in + out_tok/1e6 × $out)`.

#### 3.1 Platform list prices (published 2026-09-03)

**LangSmith:** Developer **$0**/seat, **1** seat, **5k** base traces/mo. Plus **$39**/seat/mo, unlimited seats, **10k** base traces/mo, **1** free Small serverless Deployment. Enterprise custom (hybrid/self-hosted; ABAC/RBAC). LCU **$1.50**; LSU **$1.00**. Base traces **.05¢/trace** (14 d); extended **.50¢/trace** (400 d) including base; upgrade **.45¢**. Experiments default **extended**. Tuned Evaluators **0.01 LCU** per successful evaluation run (Plus/Cloud Enterprise US public beta). Deployment: runtime **0.045 LCU/vCPU-hr**, **0.006 LCU/GiB-hr**; DB **0.177 LSU/vCPU-hr**, **0.025 LSU/GiB-hr**. Hourly ingest (Plus): **500k** events/hr; **5.0 GB**/hr. Max runs / trace: **25,000**. Plus seat + 10k traces is the **floor**, not eval cost. Engine ~**5–30 LCU**/run every **6 h** if enabled → **$7.50–$45** per Engine cycle **[inferred from $1.50 × 5–30]**.

**Braintrust:** Starter **$0** (1 GB processed, **10k** scores, 14-day, then **$4/GB**, **$2.50/1k** scores). Pro **$249**/mo (5 GB, **50k** scores, 30-day, then **$3/GB**, **$1.50/1k** scores, retention **$0.50/GB/mo** after 30 d). Unlimited users. Model credits **$10** / **$100** then token rates.

**W&B:** Free **$0**, up to **5** model seats, **5 GB** storage, **1 GB/mo** Weave ingest. Pro **from $60/mo**, ≤10 seats, **100 GB**, **1.5 GB/mo** Weave; storage overage **$0.03/GB**; Weave overage **$0.10/MB**; **<50 employees** or forced Enterprise. Enterprise custom (SSO, audit logs, HIPAA option). Weave bytes = stored trace payloads, counted **once** at ingest.

**Hugging Face Hub:** PRO **$9/mo**; Team **$20/user/mo**; Enterprise **$50/user/mo**. Hub storage **$8–12/TB/mo** public, **$12–18/TB/mo** private. Inference Endpoints from **$0.033/hour** (CPU) to **$9.25/hour** (1× B200). **Not** a promotion workflow SKU — pay for **hosting and collaboration**.

**MLflow:** Apache OSS self-host (you pay compute). Databricks UC: platform SKU — **> ⚠️ Gap** for UC $ per registered model.

**OpenAI hosted Evals:** shutting down **2026-11-30**; do not budget it as a 2027 gate. Token cost of evals remains on the **model API**. Read-only **2026-10-31**. If CI still calls hosted Evals after shutdown, **merges fail closed** while ChatGPT still works.

**Anthropic:** evals in Console consume ordinary Messages tokens; prompt cache **1.25× write / 0.1× read** (5 min) — promote busts prefix cache (03). Flex/Batch on Bedrock for **offline** evals.

#### 3.2 Worked `$ per 1k` — CI golden-set gate **[inferred]**

**(a) Task only (Luna, 2,000 in / 400 out):** `2000×$0.20/1e6 + 400×$1.20/1e6 = $0.00088/call` → **$0.88 / 1k**.

**(b) Luna judge ×1 (1,500 in / 150 out):** `1500×$0.20/1e6 + 150×$1.20/1e6 = $0.00048` → **$0.48 / 1k**. Total **$1.36 / 1k**.

**(c) Luna judge majority-of-3 (04 CI ship):** judge **$1.44 / 1k** → total **$2.32 / 1k**.

**(d) Sonnet 4.6 judge majority-of-3:** `1500×$3/1e6 + 150×$15/1e6 = $0.00675` ×3 = **$20.25 / 1k** judge → total **$21.13 / 1k**.

| Configuration | Task $ | Judge $ | **Total / 1k tasks [inferred]** |
| --- | --- | --- | --- |
| Luna only, k=1, no judge | **$0.88** | $0 | **$0.88** |
| Luna + Luna judge ×1 | $0.88 | **$0.48** | **$1.36** |
| Luna + Luna judge **majority-of-3** | $0.88 | **$1.44** | **$2.32** |
| Luna + Sonnet 4.6 majority-of-3 | $0.88 | **$20.25** | **$21.13** |
| + 10% flake retries on **judge only** | — | ×1.1 | **$2.46** / **$23.16** |

Miller **n ≈ 969** to claim 3 pp (04): budget ~**$2.25** Luna+Luna-m3 or ~**$20.50** with Sonnet-m3 for a **powered** gate **[inferred]** (`969/1000 × $2.32` / `$21.13`). A **50-item** PR smoke (`bt eval --first 20` or 50 rows) is **~$0.04–$1.06** (`50/1000 × $0.88` … `50/1000 × $21.13`) but **cannot** underwrite a 3 pp promote (04).

**Platform on top of model $ [inferred]:** LangSmith 1k-example experiment ≈ 1k traces at **extended** default → **~$5.00** (**.50¢ × 1000**) plus model $, plus child LLM/judge runs if each is a separate billable trace (count your tree). Plus includes 10k base — a nightly 2k-example eval **eats the allotment in 5 nights** if each example is one trace **[inferred]**. Braintrust: 1k tasks × 3 scores = 3k scores; Pro includes 50k → **$0** score overage; overage **$1.50/1k** scores on Pro. For 8 engineers, LS seats **$312/mo** (`8 × $39`) before traces; Braintrust Pro still **$249** **[inferred arithmetic]**.

#### 3.3 Canary / shadow — 1M req/mo Luna baseline **[inferred]**

Same 2k/400 Luna shape → 2B in × $0.20/M = **$400**; 0.4B out × $1.20/M = **$480**; baseline **$880/mo**.

| Policy | Extra model $ | Notes |
| --- | --- | --- |
| 5% canary, **same SKU** (prompt-only) | **~$0** | Split does not duplicate tokens |
| 5% canary, treatment **2×** SKU | **+$44/mo** | 5% of $880 if treatment is 2× |
| 100% promote, 2× SKU | **+$880/mo** | Full cutover |
| 5% shadow | **+$44** | 2× model $ on the 5% slice |
| 20% shadow | **+$176** | |
| 100% shadow | **+$880** | Full 2× — do not leave this on |
| 5% canary + Luna judge @ 10% of canary (5,000 judged) | **+$2.40/mo** judge **[inferred]** | 5 × $0.48 / 1k |

Fine-tune shadow on Bedrock Flex/Batch if user p99 must stay on Standard/Priority.

#### 3.4 Caching on prompt promote (cite 03; one paragraph + this module’s keys)

Exact prefix / hosted-prompt / KV caches key the **left-hand token bytes** ([03-caching.md](03-caching.md): TTL, write/read multipliers, min-token silent no-ops). A new `prompt_hash` changes the system prefix → compulsory miss and (Anthropic/OpenAI) **write-rate** (**1.25×** / **2×**) on the new prefix until the cache fills. Semantic caches that omit `prompt_hash` from the key will **serve the old completion**. Include `(model_id, prompt_hash, schema_hash, decoding_params)` in the **application cache key**; on promote, bump a generation counter or set TTL 0 for that key; expect a **stampede** of cache writes — pre-warm the new prefix if the system prompt is large. Thinking-budget / extended-thinking config changes also invalidate Anthropic caches — pin `decoding_params` in the same release. **MLflow alias prompt cache is 60 s** — replicas can serve the previous `@production` pointer for up to **60,000 ms** after `set_prompt_alias`. Flag SDK is local; do not confuse alias TTL with flag TTL.

#### 3.5 Latency — **two clocks**, numeric ms

> ⚠️ Gap: Braintrust / LangSmith / Promptfoo publish **no** p50/p95/p99 for “an eval job.” Do not invent a vendor SLO. Numbers below are **[inferred] policy** from TPM + majority-of-3 + flake (CI clock) and from flag-SDK + 16’s extraction SLO (user clock).

**Clock A — user path (must not include judge).** Flag SDK evaluation is local/cached. **[inferred]** on top of the data-plane: **p50 1 ms / p95 10 ms / p99 50 ms**. Cite 16 extraction **p50 1,200 / p95 4,000 / p99 12,000 ms [inferred]** as the inference SLO this module **must not inflate**. Mitigations: fail-open to **last-resolved bundle** if the flag service is down; sticky `thread_id` so a promote does not swap `prompt_hash` mid-conversation; never call the CI judge on the request thread; never share canary TPM with the eval-job pool (bulkhead).

**Clock B — CI eval job** (1k golden, Luna + majority-of-3, parallel). No vendor p99. **[inferred policy]** **p50 180,000 ms** (3 min) / **p95 600,000 ms** (10 min) / **p99 1,800,000 ms** (30 min) from TPM queues + m3 + flake retries. PR smoke `--first N`: **p50 30,000–60,000 ms**. LangGraph `/ok` poll **30,000 ms** then fail is **health**, not eval SLO — a green `/ok` with a red golden suite still **blocks merge**.

**Promote TTL (time until replicas see the new pointer):**

| Mechanism | Time | Use |
| --- | --- | --- |
| Feature flag | **seconds** (SDK cache TTL) | Incident kill; prompt-only |
| MLflow alias prompt cache | **60,000 ms** | Alias retag |
| Hub client cache | community ~5 min — **> ⚠️ Gap, unverified** | Do not bet RTO on it |
| Vertex / SageMaker endpoint update | **minutes-class [inferred]** | Weight/endpoint deploy |

Rollback: **flag first**, alias second, image last (**~20 min** rebuild).

**Mitigations per user-path tier.**

| Tier | Lever |
| --- | --- |
| **p50** | Local flag SDK; last-resolved bundle in-process; no Hub pull on the request |
| **p95** | Sticky thread; alias cache awareness (60 s); do not block on registry GET |
| **p99** | Fail-open flags with defaults; circuit-open registry → last bundle; never wait on CI |

#### 3.6 Throughput and back-pressure

| Bottleneck | Limit | Back-pressure |
| --- | --- | --- |
| LangSmith ingest | **5k POST /runs per min** (SDK batches ≤100); Plus **500k events/hr** | Batch SDK; sample online eval; cassette cache in CI |
| Model API TPM/RPM | per SKU (16); evals compete if you share a project | **Bulkhead:** CI queue vs user TPM. Offline evals on Bedrock Flex/Batch |
| CI concurrency | GitHub/GitLab runners + judge RPM | `--first N` on PR; full suite on merge; serialize promote |
| W&B Weave ingest | plan GB/mo; overage **$0.10/MB** | Sample production calls |

SC/m3 **triples** judge TPM of the **job**, not the user. Compile jobs (16) **must not** share online TPM. Back-pressure: shed **online sample rate** before shedding the **merge gate**; never skip the gate.

#### 3.7 Availability, RPO/RTO

Product SLO = **model API + app**. Bedrock Reserved **99.5%** is a **capacity** uptime target, not LangSmith, not your flag service.

| Quantity | Target | Mechanism |
| --- | --- | --- |
| Serving during control-plane outage | Last resolved bundle | Flag SDK fail-open; local alias cache |
| CI red / prod green | **Correct** | Gate is not a liveness probe |
| **RPO** (release) | Last merge | git + immutable version; you cannot “almost” promote |
| **RTO** (canary kill) | **seconds** | Flag 0% candidate |
| **RTO** (alias) | **60,000 ms** class (MLflow) / minutes (endpoints) | Retag previous hash; keep prior package |
| **RTO** (image) | **~20 min** | Last resort; flags in front of the graph so prompt-only never takes this path |
| OpenAI Fast mode **99.9%** | Throughput/uptime of **chat**, not promotion | Do not cite as LLMOps SLO |

#### 3.8 Compliance and trade-offs

Golden sets are **training-adjacent** legal records. LangSmith datasets have **indefinite** retention once added — adding a raw prod trace to a dataset is a **retention extension forever**. Redact first. W&B Enterprise: HIPAA **option**. LangSmith Enterprise hybrid/self-hosted for data plane. Hosted Hub playground is a **data residency** decision. OpenAI Evals sunset is **vendor-concentration** for the **gate**, not for chat (04). Trade-off: Hub velocity vs git CODEOWNERS. Trade-off: Luna-m3 **$2.32 / 1k** vs Sonnet-m3 **$21.13 / 1k** as the ship judge. Trade-off: 5% canary blast-radius vs Miller power (canary is not the 3 pp proof). Trade-off: extended traces **.50¢** vs 14-day base **.05¢** — experiments default extended.

---

### 4. Distributed Resilience & Security

#### 4.1 Durable promotion: Temporal / compare-and-swap

Treat `registry.set_alias` / Hub tag move as a **compare-and-swap** workflow, not a curl from a laptop.

| Step | Checkpoint | Idempotency |
| --- | --- | --- |
| Eval job | Pin dataset version, scorer version, bundle hashes, seed | Re-running CI must not pick `@latest`. LangSmith pytest `LANGSMITH_TEST_CACHE` — **bust the cassette** when `prompt_hash` changes. Braintrust `--first N` is **non-final** |
| Register version | Immutable version id / SHA | Content-addressed; retry-safe |
| Stage alias | `staging` / `@challenger` | Pointer write; prod alias unchanged |
| Canary | Flag variation + salt | Sticky assignment is a pure hash |
| Promote | CAS: `(who, prev_hash, new_hash, experiment_id)` | If-Match / etag. Lost CAS → refresh, do not blind-set |
| Rollback | Flag 0% then retag prev_hash | Previous `@champion` **undeleted** |

Databricks: only principals with read on staging + write on prod can `copy_model_version`. W&B **protected** aliases block Members from unlinking production. LangSmith **Owners only** is the analogous lock. SageMaker/Vertex: keep prior endpoint variant. LangSmith Deployment: previous **revision** remains listable — switching revision is slower than a flag; use flags **in front of** the graph when the change is prompt-only (13 hosts the graph; flags select the bundle).

**Dead-letter:** eval rows that 400 on schema or exceed context go to a quarantine split — not a retry storm that makes every PR red. Poison always-fail rows: metadata-quarantine; do not delete without audit.

#### 4.2 Failure taxonomy

| Class | Examples | Detection | Handling |
| --- | --- | --- | --- |
| **Transient (flake)** | Judge 429/5xx, LangSmith 5k POST/min, TPM | Error-rate bucket **separate** from quality (04) | Full-jitter retry on **idempotent score HTTP**. Do **not** retry the user path. Do **not** skip the merge gate |
| **Real metric drop** | Hard-oracle / task metric down vs pinned suite | CI gate; paired A vs B on same task IDs (04) | Fail-closed; do not merge. Majority-of-3 and order-swap are for **CI**, not prod |
| **Poison golden** | PAN in a fixture; public MTEB items in the gate set (15); employees’ favorite examples the prompt was tuned on (16/04 judge-as-loss); adversarial always-fail rows | Hold-out drop; online ≠ CI; every PR red → teams skip the gate | Detect→redact→audit **before** git/Hub. Separate **optimize** vs **gate** suites (04/16). Sealed split. Quarantine with metadata |
| **Label drift** | Dataset `as_of` mismatch vs `eval_suite_id` on the release | Irreproducible experiment | Pin version/tag; record suite_id on the tuple |
| **Idempotency** | Two `set_alias` races; promote twice | Split-brain alias | CAS / If-Match; single-writer Temporal |
| **Denial of wallet** | Nightly extended-trace evals; 100% shadow; Sonnet-m3 on every PR | Invoice | Opt out per evaluator; `--first N` on PR; Flex/Batch for offline |
| **Permanent** | Vertex default auto-move; alias → deleted version; HF tag moved; Bedrock PT mistaken for a version | 404 / silent default swap | Pin **version numbers/SHAs** in the release record; GC retains last N prod versions; **never** delete the version `production` still names |

T=0 is not deterministic enough to skip statistical tests (04). Distinguish infra-error bucket from quality drop.

#### 4.3 Circuit breaker closed → open → half-open

Independent breakers on the **CI judge API** and the **registry API**. A 429 on the judge must not stall **prod serving** and must not **skip the merge gate**. Canary kill = **flag 0%**, not “open the registry breaker.”

```
        judge 429/5xx | registry 5xx | alias CAS conflict | ingest 429
  ┌──────────┐  ─────────────────────────────────────────────────►  ┌──────────┐
  │  CLOSED  │                                                       │   OPEN   │
  │  judge / │  success resets consecutive count                     │ FAIL FAST│
  │  registry│                                                       │ fallback │
  └────┬─────┘                                                       │ chain    │
       ▲                                                             └────┬─────┘
       │ probe OK                                                         │ cooldown
       │                                                            ┌─────▼──────┐
       └──────────── probe allow ───────────────────────────────────│ HALF-OPEN  │
                    probe fail → stay OPEN                          │ 1 canary   │
                                                                    │ (not user) │
                                                                    └────────────┘
```

**Thresholds [policy, not vendor SLO]:**

| Trip | Closed → open | Half-open | Fallback (**gate-stable**) |
| --- | --- | --- | --- |
| **CI judge API** 429/5xx | consecutive ≥ **5** or error-rate window | One tiny score of a **canary example** — not a user blob | Retry with jitter; if still open: **fail the job** (merge blocked). **Never** skip the merge gate. Do **not** promote from `--first N` |
| **Online sidecar judge** | same | same | **Skip score**, decrement **coverage%** (04/05). Prod stays up. Coverage collapse ≠ quality green |
| **Registry API** 5xx / timeout | consecutive ≥ **3** | One `get` of a known version | Serve **last-known bundle** from local cache. Fail-open flags. Do not pull `latest` |
| **Alias CAS lost** | n/a (conflict is expected) | Refresh etag | Retry CAS; never blind `set_alias(production)` |
| **Canary hard-oracle / 5xx** | n/a | n/a | **Flag 0%** within SDK TTL. Keep `@champion` undeleted |
| **LangSmith hourly 429** | ingest window | Batch / sample | CI red, prod green is correct |

**Fallback chain (required interview answer):** **last-known bundle (fail-open flags) → prior `@champion` alias → previous Deployment revision / endpoint variant.** I would not skip the CI gate. I would not put the judge on the user path. Dual hashes stay sticky on `thread_id` until the canary graduates or dies.

#### 4.4 Zero-Trust MCP (this module — not deferred)

Agents will wrap this layer as MCP tools: **`eval.run`**, **`registry.set_alias`**, **`promote_tag`**. There is **no module 19**. Zero-Trust for **promotion** lives **here**. 16 documents authoring tools (`prompts/get`, `prompts/set`, `compile_program`); this file is the PEP for **alias moves**. MCP is **not** the registry PEP — **IAM / UC grants / W&B Owners / Hub Owners-only / protected aliases** are. The gateway is the PEP **in front of** Hub, MLflow, SageMaker, and Vertex.

MCP delivers **tool descriptions into context** (LLM01) and **`tools/call`** (side effects). `promote_tag` / `registry.set_alias` on `production` is a **control-plane write**. A hallucinated `version` **404s** — never `@latest`.

**Three trust boundaries:** (1) model ↔ host — model cannot verify that `eval.run` is not `promote_tag`; (2) client ↔ MCP server — authN/Z + integrity of `tools/list`; (3) MCP server ↔ Hub/MLflow/SageMaker — the server is a deputy. CVE-2025-6514 CVSS **9.6**: **connecting** to hostile `authorization_endpoint` metadata can be RCE before any tool call. CVE-2025-54136 (MCPoison) CVSS **8.8**: no re-validate of tool JSON.

**Zero-Trust minimum on promote tools:**

| Control | Spec | On this promotion layer |
| --- | --- | --- |
| **Transport** | OAuth 2.1 + PKCE `S256`. RFC **8707** `resource` = **canonical MCP server URI** on authorize *and* token. Servers accept only tokens whose audience is themselves. **MUST NOT** passthrough the client token to Hub/MLflow/SageMaker; obtain a new token (typically RFC **8693** exchange) scoped to the upstream | Gateway holds registry service credentials. A static `Authorization: Bearer` reused upstream is still passthrough. **No Hub/MLflow token passthrough** |
| **Capability** | `initialize` + tool list; Streamable HTTP `Mcp-Method`/`Mcp-Name` so the gateway can authz per tool without parsing JSON-RPC | Allowlist: `eval.run` on **staging** suites for assistants; `registry.set_alias` / `promote_tag` = **human + break-glass only** |
| **Hash-pin** | `toolSurfaceHash` over canonical JSON of **name + description + inputSchema (+ outputSchema)**. Re-verify every `tools/call`. Mismatch → session pause. Also hash-pin **server commands** (argv/image digest) | Arguments: `name`, `prompt_hash`, `etag` — **identity from RunContext**, not JSON `actor`. Allowlist hashes that passed CI (`eval_suite_id` in attestation). Deny `prompts/get` of non-promoted hashes in prod runtime (16 sample runtime) |
| **Identity** | Verified access token. **Never** the LLM | Promoter principal from IdP. Tool arg `actor` is a **proposal to discard** |

**No token passthrough:** the MCP server must not forward the user’s IdP access token to LangSmith Hub or MLflow tracking. Promote jobs use a **scoped** service credential. Hash-pin the MCP **server** so a swapped binary cannot `set_alias`.

**Tool-level RBAC (least privilege):**

| Tool | Who | Allowed | Forbidden |
| --- | --- | --- | --- |
| `eval.run` | CI; assistants **staging only** | Pinned suite + pinned VERSION; `--first N` is non-final | Prod `@latest`; promote-from-smoke; model-chosen suite |
| `registry.set_alias` | Release manager + HITL / CI after green gate | If-Match retag `staging` / `@challenger`; `production` only with ticket + experiment id | Assistant; deleting the version prod still names |
| `promote_tag` | Release manager + HITL | Hub reserved env tags / `@champion` after gate | Skipping CI; shared “prod” API key in a laptop playground |
| Read version / `get` | Runtime / assistant | Already-promoted hash | Draft hashes; other tenants |
| Delete version | GC job + break-glass | Versions **not** named by prod aliases; after retention | Model-called delete; delete-default on Vertex without pinning |

One tool, one verb. Credentials **never** in model-visible context. HITL for: `promote_tag`, `set_alias` to production, new MCP server registration, break-glass merge without CI green (ticket + automatic post-change golden run + page).

**MCP is not the registry PEP.** A correctly gated `promote_tag` that still uses a workspace Hub token with `prompts:tag` for everyone is a leak. Isolation is Owners-only / protected aliases / IAM / UC grants. Break-glass: named role may move `production` **without** CI green **only** with a ticket + automatic post-change golden run + page. Log the actor. Never a shared prod key.

Audit (WORM): `(ts, actor_id, action=promote|rollback, prompt_hash, schema_hash, model_id, decoding_params, index_gen, eval_suite_id, experiment_id, prev_hash, ticket_id)` — **not** raw prompt bodies, **not** few-shot PII, **not** golden example text. LangSmith webhook `created_by` + `commit_hash`. Databricks UC **system tables**. W&B Enterprise **audit logs**. SageMaker CloudTrail on `UpdateModelPackage`. HF Enterprise: SSO + audit logs (list **$50/user/mo**).

#### 4.5 PII pipeline — detect → redact → audit **before goldens enter git/Hub**

Golden examples are **training-adjacent**. Poison = real PAN in a fixture that then leaks via traces (05). LangSmith dataset retention is **indefinite** once added — redact **before** the example enters the dataset.

**Pipeline (explicit), on every golden row and every Hub/MLflow prompt blob, before commit:**

1. **Detection (control plane).** Dual-gate: **regex** (email, PAN, SSN, phones) + **ML NER** if available. Scan: git fixtures, Hub commits, MLflow prompt text, eval dataset rows, MCP `eval.run` payloads, log lines. If ML is down: **fail closed (block)** on PAN/SSN into git/Hub — do not “add it to the golden set and DLP later.”
2. **Redaction.** Stable tokens (`[EMAIL_<hash12>]`, `[PAN]`) so task labels still match; `block` when policy says the field must not exist. Do not store raw prod traces as goldens if the Hub/LangSmith ACL is weaker than the source.
3. **Audit trail (WORM).** Decisions, not values: `content_sha256` pre/post, entity **types** + counts, action (`redact` / `mask` / `block-from-golden`), detector, `correlation_id`, `eval_suite_id`. A promote without DLP attestation on the suite is a control-plane bug. **GDPR erasure:** delete the example blob **and** the audit digest policy (legal hold) — not only the chat UI.

---

### 5. Production Enterprise Code

Self-contained stdlib. Optional `mlflow` / `langsmith` / `launchdarkly` imports unused. Same control flow without keys: retries + full jitter, circuit breaker **closed → open → half-open** on **CI judge API** and **registry API**, CI gate **fail-closed** on metric drop, canary assignment **sticky** on `thread_id`, fallback **last-known bundle**, structured logs with **hashes not prompt bodies**, **never** log golden PII. Promote is CAS. Identity from **auth context**, not tool JSON `actor`. Run: `python llmops_promote_runtime.py`.

```python
#!/usr/bin/env python3
"""Promotion control plane: CI gate, sticky canary, alias CAS, last-known bundle.

Never skip the merge gate. Never log prompt bodies or golden PII.
Run: python llmops_promote_runtime.py
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import time
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Callable

EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
PAN_RE = re.compile(r"\b(?:\d[ -]*?){13,19}\b")
MLFLOW_ALIAS_TTL_S = 60.0
FLAG_TTL_S = 2.0
METRIC_FLOOR = 0.85


class CorrelationFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        for k in (
            "correlation_id", "tenant_id", "prompt_hash", "schema_hash",
            "model_id", "eval_suite_id", "experiment_id",
        ):
            setattr(record, k, getattr(record, k, "-"))
        return True


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("llmops_promote")
    if logger.handlers:
        return logger
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter(
        '{"ts":"%(asctime)s","level":"%(levelname)s","cid":"%(correlation_id)s",'
        '"tenant":"%(tenant_id)s","prompt_hash":"%(prompt_hash)s",'
        '"schema_hash":"%(schema_hash)s","model_id":"%(model_id)s",'
        '"suite":"%(eval_suite_id)s","exp":"%(experiment_id)s","msg":"%(message)s"}'
    ))
    h.addFilter(CorrelationFilter())
    logger.addHandler(h)
    logger.setLevel(logging.INFO)
    return logger


LOG = configure_logging()


def slog(level: int, msg: str, **extra: Any) -> None:
    LOG.log(level, msg, extra=extra)


def retry_call(
    fn: Callable[[], Any],
    *,
    attempts: int = 3,
    base_s: float = 0.2,
    cap_s: float = 2.0,
    retryable: tuple[type[BaseException], ...] = (TimeoutError, ConnectionError),
) -> Any:
    last: BaseException | None = None
    for i in range(attempts):
        try:
            return fn()
        except retryable as exc:
            last = exc
            if i == attempts - 1:
                break
            sleep_s = random.random() * min(cap_s, base_s * (2**i))
            slog(logging.WARNING, f"retry_backoff attempt={i + 1} sleep_s={sleep_s:.3f}")
            time.sleep(sleep_s)
    assert last is not None
    raise last


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(RuntimeError):
    pass


class GateFailed(RuntimeError):
    pass


class CasConflict(RuntimeError):
    pass


class PermanentRegistryError(RuntimeError):
    pass


@dataclass
class CircuitBreaker:
    name: str
    failure_threshold: int = 5
    cooldown_s: float = 30.0
    half_open_probes: int = 1
    _state: CircuitState = CircuitState.CLOSED
    _failures: int = 0
    _opened_at: float = 0.0
    _probes_used: int = 0

    def allow(self) -> None:
        if self._state is CircuitState.OPEN:
            if time.monotonic() - self._opened_at >= self.cooldown_s:
                self._state, self._probes_used = CircuitState.HALF_OPEN, 0
            else:
                raise CircuitOpenError(f"circuit_open:{self.name}")
        if self._state is CircuitState.HALF_OPEN:
            if self._probes_used >= self.half_open_probes:
                raise CircuitOpenError(f"circuit_half_open_busy:{self.name}")
            self._probes_used += 1

    def record_success(self) -> None:
        self._failures, self._probes_used, self._state = 0, 0, CircuitState.CLOSED

    def record_failure(self) -> None:
        self._failures += 1
        if self._state is CircuitState.HALF_OPEN or self._failures >= self.failure_threshold:
            self._state, self._opened_at = CircuitState.OPEN, time.monotonic()


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def pii_detect_redact_audit(
    text: str, *, audit: list[dict[str, Any]], correlation_id: str, tenant_id: str,
    sink: str, block_on_pan: bool = True,
) -> str:
    kinds = [k for k, rx in (("email", EMAIL_RE), ("pan", PAN_RE)) if rx.search(text)]
    pre = _sha(text)
    row = {"cid": correlation_id, "tenant": tenant_id, "sink": sink, "kinds": kinds, "detector": "regex"}
    if "pan" in kinds and block_on_pan and sink in {"golden", "hub_commit", "mcp_eval"}:
        audit.append({**row, "action": "block-from-golden", "pre": pre, "post": _sha("")})
        raise PermissionError(f"pii_block:{sink}:pan")
    redacted = EMAIL_RE.sub(
        lambda m: f"[EMAIL_{hashlib.sha256(m.group().encode()).hexdigest()[:12]}]", text
    )
    redacted = PAN_RE.sub("[PAN]", redacted)
    audit.append({**row, "action": "redact" if redacted != text else "allow", "pre": pre, "post": _sha(redacted)})
    return redacted


@dataclass(frozen=True)
class ReleaseBundle:
    model_id: str
    prompt_hash: str
    schema_hash: str
    decoding_params: str
    index_gen: str
    eval_suite_id: str
    version: int = 0

    def tuple_id(self) -> str:
        raw = "|".join([
            self.model_id, self.prompt_hash, self.schema_hash,
            self.decoding_params, self.index_gen, self.eval_suite_id, str(self.version),
        ])
        return "rel_" + _sha(raw)[:16]


@dataclass(frozen=True)
class AuthContext:
    principal: str
    roles: frozenset[str]
    tenant_id: str


def sticky_canary(thread_id: str, salt: str, pct: int) -> bool:
    if pct <= 0:
        return False
    if pct >= 100:
        return True
    h = int(_sha(f"{salt}:{thread_id}")[:8], 16)
    return (h % 100) < pct


@dataclass
class FlagStore:
    pct: int = 0
    salt: str = "exp-1"
    candidate: ReleaseBundle | None = None
    _resolved: ReleaseBundle | None = None
    _resolved_at: float = 0.0

    def assign(self, thread_id: str, control: ReleaseBundle) -> ReleaseBundle:
        cand = self.candidate or control
        bundle = cand if sticky_canary(thread_id, self.salt, self.pct) else control
        self._resolved, self._resolved_at = bundle, time.monotonic()
        return bundle

    def last_resolved(self, control: ReleaseBundle) -> ReleaseBundle:
        if self._resolved is not None and (time.monotonic() - self._resolved_at) < FLAG_TTL_S * 30:
            return self._resolved
        return control

    def kill(self) -> None:
        self.pct = 0


@dataclass
class Registry:
    versions: dict[int, ReleaseBundle] = field(default_factory=dict)
    aliases: dict[str, int] = field(default_factory=dict)
    etags: dict[str, str] = field(default_factory=dict)
    _alias_cache: dict[str, tuple[ReleaseBundle, float]] = field(default_factory=dict)
    _next: int = 1
    fail_kind: str | None = None

    def register(self, bundle: ReleaseBundle) -> ReleaseBundle:
        ver = self._next
        self._next += 1
        pinned = replace(bundle, version=ver)
        self.versions[ver] = pinned
        return pinned

    def set_alias(self, name: str, version: int, *, etag: str | None, actor: str) -> str:
        if self.fail_kind == "transient":
            raise TimeoutError("registry_timeout")
        if self.fail_kind == "permanent":
            raise PermanentRegistryError("alias_deleted")
        if version not in self.versions:
            raise PermanentRegistryError("unknown_version")
        current = self.etags.get(name, "")
        if etag is not None and current and etag != current:
            raise CasConflict(f"cas_lost:{name}")
        self.aliases[name] = version
        new_etag = _sha(f"{name}:{version}:{actor}")[:12]
        self.etags[name] = new_etag
        self._alias_cache.pop(name, None)
        return new_etag

    def resolve(self, name: str, *, use_cache: bool = True) -> ReleaseBundle:
        if use_cache and name in self._alias_cache:
            bundle, ts = self._alias_cache[name]
            if time.monotonic() - ts < MLFLOW_ALIAS_TTL_S:
                return bundle
        if name not in self.aliases:
            raise PermanentRegistryError(f"no_alias:{name}")
        bundle = self.versions[self.aliases[name]]
        self._alias_cache[name] = (bundle, time.monotonic())
        return bundle


@dataclass
class JudgeClient:
    fail_kind: str | None = None
    calls: int = 0

    def score(self, *, suite_id: str, version: str) -> float:
        if version in {"@latest", "latest"}:
            raise PermanentRegistryError("unpinned_latest")
        self.calls += 1
        if self.fail_kind == "transient":
            raise TimeoutError("judge_429")
        if self.fail_kind == "drop":
            return 0.40
        return 0.91 if suite_id else 0.0


@dataclass
class PromoteRuntime:
    registry: Registry
    flags: FlagStore
    judge: JudgeClient
    judge_breaker: CircuitBreaker = field(default_factory=lambda: CircuitBreaker("ci_judge"))
    registry_breaker: CircuitBreaker = field(default_factory=lambda: CircuitBreaker("registry", failure_threshold=3))
    audit: list[dict[str, Any]] = field(default_factory=list)
    worm: list[dict[str, Any]] = field(default_factory=list)
    last_known: ReleaseBundle | None = None
    online_coverage: float = 1.0

    def ingest_golden(self, text: str, *, auth: AuthContext, cid: str) -> str:
        return pii_detect_redact_audit(
            text, audit=self.audit, correlation_id=cid, tenant_id=auth.tenant_id, sink="golden"
        )

    def eval_run(
        self, *, auth: AuthContext, suite_id: str, version: str, smoke: bool, cid: str, experiment_id: str,
    ) -> float:
        if "eval" not in auth.roles and "assistant" not in auth.roles:
            raise PermissionError("eval_denied")
        if "assistant" in auth.roles and not suite_id.startswith("staging:"):
            raise PermissionError("eval_staging_only")
        if smoke and "promote" in auth.roles:
            raise GateFailed("refuse_promote_from_smoke")

        def _call() -> float:
            self.judge_breaker.allow()
            try:
                s = self.judge.score(suite_id=suite_id, version=version)
            except (TimeoutError, ConnectionError):
                self.judge_breaker.record_failure()
                raise
            self.judge_breaker.record_success()
            return s

        try:
            score = retry_call(_call)
        except CircuitOpenError:
            slog(logging.ERROR, "ci_judge_circuit_open_fail_closed", correlation_id=cid, eval_suite_id=suite_id, experiment_id=experiment_id)
            raise GateFailed("merge_blocked_judge_unavailable")
        slog(
            logging.INFO, f"eval_ok score={score:.3f} smoke={smoke}",
            correlation_id=cid, eval_suite_id=suite_id, experiment_id=experiment_id,
        )
        return score

    def ci_gate(
        self, *, auth: AuthContext, bundle: ReleaseBundle, cid: str, experiment_id: str, smoke: bool = False,
    ) -> None:
        if "ci" not in auth.roles:
            raise PermissionError("ci_denied")
        score = self.eval_run(
            auth=AuthContext(auth.principal, frozenset({"eval"}), auth.tenant_id),
            suite_id=bundle.eval_suite_id, version=str(bundle.version or "pending"),
            smoke=smoke, cid=cid, experiment_id=experiment_id,
        )
        if score < METRIC_FLOOR:
            slog(logging.ERROR, f"ci_gate_fail score={score:.3f}", correlation_id=cid, prompt_hash=bundle.prompt_hash, schema_hash=bundle.schema_hash, model_id=bundle.model_id, eval_suite_id=bundle.eval_suite_id, experiment_id=experiment_id)
            raise GateFailed(f"metric_drop:{score:.3f}<{METRIC_FLOOR}")
        slog(logging.INFO, "ci_gate_pass", correlation_id=cid, prompt_hash=bundle.prompt_hash, schema_hash=bundle.schema_hash, model_id=bundle.model_id, eval_suite_id=bundle.eval_suite_id, experiment_id=experiment_id)

    def online_score_or_skip(self, *, cid: str) -> float | None:
        try:
            self.judge_breaker.allow()
            s = self.judge.score(suite_id="online:sidecar", version="pinned")
            self.judge_breaker.record_success()
            return s
        except (CircuitOpenError, TimeoutError, ConnectionError):
            self.online_coverage = max(0.0, self.online_coverage - 0.05)
            slog(logging.WARNING, f"online_skip coverage={self.online_coverage:.2f}", correlation_id=cid)
            return None

    def _registry_write(self, fn: Callable[[], Any]) -> Any:
        def _call() -> Any:
            self.registry_breaker.allow()
            try:
                out = fn()
            except (TimeoutError, ConnectionError):
                self.registry_breaker.record_failure()
                raise
            except PermanentRegistryError:
                self.registry_breaker.record_failure()
                raise
            self.registry_breaker.record_success()
            return out
        return retry_call(_call)

    def promote_tag(
        self, *, auth: AuthContext, alias: str, bundle: ReleaseBundle, prev_hash: str,
        experiment_id: str, ticket_id: str, cid: str,
    ) -> str:
        if "promote" not in auth.roles:
            raise PermissionError("promote_denied")
        pinned = bundle if bundle.version else self.registry.register(bundle)
        try:
            etag = self._registry_write(
                lambda: self.registry.set_alias(
                    alias, pinned.version, etag=prev_hash or None, actor=auth.principal,
                )
            )
        except CircuitOpenError:
            slog(logging.WARNING, "registry_open_last_known_bundle", correlation_id=cid, experiment_id=experiment_id)
            if self.last_known is None:
                raise
            return "last_known"
        self.last_known = pinned
        self.worm.append({
            "ts": time.time(), "actor_id": auth.principal, "action": "promote",
            "prompt_hash": pinned.prompt_hash, "schema_hash": pinned.schema_hash,
            "model_id": pinned.model_id, "decoding_params": pinned.decoding_params,
            "index_gen": pinned.index_gen, "eval_suite_id": pinned.eval_suite_id,
            "experiment_id": experiment_id, "prev_hash": prev_hash, "ticket_id": ticket_id,
            "alias": alias, "version": pinned.version,
        })
        slog(
            logging.INFO, f"promote_ok alias={alias} version={pinned.version} tuple={pinned.tuple_id()}",
            correlation_id=cid, prompt_hash=pinned.prompt_hash, schema_hash=pinned.schema_hash,
            model_id=pinned.model_id, eval_suite_id=pinned.eval_suite_id, experiment_id=experiment_id,
        )
        return etag

    def serve(self, *, thread_id: str, control: ReleaseBundle, cid: str) -> ReleaseBundle:
        try:
            champion = self.registry.resolve("champion")
            self.last_known = champion
        except (PermanentRegistryError, TimeoutError, CircuitOpenError):
            champion = self.last_known or control
            slog(logging.WARNING, "serve_last_known_bundle", correlation_id=cid, prompt_hash=champion.prompt_hash)
        chosen = self.flags.assign(thread_id, champion)
        slog(
            logging.INFO, f"serve_ok thread={thread_id} tuple={chosen.tuple_id()}",
            correlation_id=cid, prompt_hash=chosen.prompt_hash, schema_hash=chosen.schema_hash,
            model_id=chosen.model_id, eval_suite_id=chosen.eval_suite_id,
        )
        return chosen

    def rollback_flag_first(self, *, cid: str) -> None:
        self.flags.kill()
        slog(logging.WARNING, "canary_kill_flag_zero", correlation_id=cid)


def mcp_call(rt: PromoteRuntime, tool: str, args: dict[str, Any], *, auth: AuthContext, cid: str) -> Any:
    """Gateway PEP: identity from auth, not args['actor']. Hash-pin is the caller's job."""
    if "actor" in args:
        args = {k: v for k, v in args.items() if k != "actor"}
    if tool == "eval.run":
        return rt.eval_run(
            auth=auth, suite_id=args["suite_id"], version=args["version"],
            smoke=bool(args.get("smoke")), cid=cid, experiment_id=args.get("experiment_id", "exp"),
        )
    if tool == "registry.set_alias":
        bundle = rt.registry.versions[int(args["version"])]
        return rt.promote_tag(
            auth=auth, alias=args["alias"], bundle=bundle, prev_hash=args.get("etag", ""),
            experiment_id=args["experiment_id"], ticket_id=args.get("ticket_id", ""), cid=cid,
        )
    if tool == "promote_tag":
        bundle = rt.registry.versions[int(args["version"])]
        return rt.promote_tag(
            auth=auth, alias=args.get("alias", "champion"), bundle=bundle,
            prev_hash=args.get("etag", ""), experiment_id=args["experiment_id"],
            ticket_id=args["ticket_id"], cid=cid,
        )
    raise PermissionError(f"unknown_tool:{tool}")


def cache_key(bundle: ReleaseBundle, user_hash: str) -> str:
    return "|".join([
        bundle.model_id, bundle.prompt_hash, bundle.schema_hash, bundle.decoding_params, user_hash,
    ])


def build_runtime() -> tuple[PromoteRuntime, ReleaseBundle]:
    control = ReleaseBundle(
        model_id="gpt-5.6-luna", prompt_hash="p_aaa", schema_hash="s_v1",
        decoding_params="t0_max400", index_gen="idx-3", eval_suite_id="gate-v4",
    )
    rt = PromoteRuntime(registry=Registry(), flags=FlagStore(), judge=JudgeClient())
    pinned = rt.registry.register(control)
    rt.registry.set_alias("champion", pinned.version, etag=None, actor="bootstrap")
    rt.last_known = pinned
    return rt, pinned


if __name__ == "__main__":
    rt, control = build_runtime()
    ci = AuthContext("ci-bot", frozenset({"ci", "eval"}), "acme")
    rel = AuthContext("release", frozenset({"promote", "eval"}), "acme")
    asst = AuthContext("bot", frozenset({"assistant"}), "acme")
    cid = "cid-1"

    redacted = rt.ingest_golden("Reset MFA. Contact ada@example.com", auth=ci, cid=cid)
    assert "[EMAIL_" in redacted
    try:
        rt.ingest_golden("card 4111 1111 1111 1111", auth=ci, cid="cid-pii")
        raise SystemExit("pan should block")
    except PermissionError:
        pass

    candidate = ReleaseBundle(
        model_id=control.model_id, prompt_hash="p_bbb", schema_hash="s_v1",
        decoding_params="t0_max400", index_gen="idx-3", eval_suite_id="gate-v4",
    )
    pinned = rt.registry.register(candidate)
    rt.ci_gate(auth=ci, bundle=pinned, cid="cid-gate", experiment_id="exp-77")
    etag = mcp_call(
        rt, "promote_tag",
        {"version": pinned.version, "etag": rt.registry.etags["champion"], "experiment_id": "exp-77", "ticket_id": "INC-9", "actor": "spoofed"},
        auth=rel, cid="cid-prom",
    )
    assert rt.worm[-1]["actor_id"] == "release"
    assert "spoofed" not in json.dumps(rt.worm[-1])

    rt.flags.candidate, rt.flags.pct, rt.flags.salt = pinned, 5, "salt-a"
    a = rt.serve(thread_id="thr-1", control=control, cid="cid-s1")
    b = rt.serve(thread_id="thr-1", control=control, cid="cid-s2")
    assert a.tuple_id() == b.tuple_id()

    rt.judge.fail_kind = "drop"
    try:
        rt.ci_gate(auth=ci, bundle=pinned, cid="cid-drop", experiment_id="exp-78")
        raise SystemExit("drop should fail-closed")
    except GateFailed as exc:
        assert "metric_drop" in str(exc)

    rt.judge.fail_kind = "transient"
    rt.judge_breaker = CircuitBreaker("ci_judge", failure_threshold=1, cooldown_s=60)
    try:
        rt.ci_gate(auth=ci, bundle=pinned, cid="cid-flake", experiment_id="exp-79")
        raise SystemExit("judge outage should block merge")
    except GateFailed:
        pass
    skipped = rt.online_score_or_skip(cid="cid-online")
    assert skipped is None and rt.online_coverage < 1.0

    rt.rollback_flag_first(cid="cid-kill")
    assert rt.flags.pct == 0
    served = rt.serve(thread_id="thr-1", control=control, cid="cid-after")
    assert served.prompt_hash in {"p_aaa", "p_bbb"}

    try:
        mcp_call(rt, "eval.run", {"suite_id": "prod:gate-v4", "version": "1"}, auth=asst, cid="cid-asst")
        raise SystemExit("assistant must not eval prod")
    except PermissionError:
        pass

    key = cache_key(pinned, "userhash")
    assert pinned.prompt_hash in key and "system prompt text" not in key
    print("ok", len(rt.worm), "worm rows", len(rt.audit), "pii rows")
```

**Wiring notes (not in the script):** production MLflow loads `prompts:/name/VERSION` during eval, never `@production` mid-flight. Hub pull is `name:commit_hash`. LaunchDarkly AgentControl returns the variation; the **app** calls the provider. Temporal wraps `promote_tag` with If-Match and stores experiment id in workflow state — not prompt bodies. Gateway hash-pins MCP tool JSON; denies `promote_tag` for assistant tokens. Cassette cache busts when `prompt_hash` changes. Vertex: never delete the `default` version expecting a hard pin. Bedrock PT is purchased **after** the bundle’s `model_id` is chosen.

---

### 6. Architectural System Design Scenarios

#### Scenario 1 — Prompt-only SaaS (no fine-tune)

**Problem.** B2B copilot, one provider `model_id` (Luna), weekly prompt iteration, golden set hundreds not millions, LangGraph **or** a simple gateway. PMs want Hub playground velocity. Security wants git audit and CODEOWNERS. Product wants 5% blast-radius and flag kill in seconds. Nobody should rebuild Agent Server to change a system prompt (13). 50-item smoke cannot underwrite 3 pp (04).

**Proposed architecture (recommended B):**

```
  ┌─────────┐   ┌─────────────────────────────────────────────────────────┐
  │ git SoT │──▶│ CONTROL: PR → pytest-langsmith / promptfoo / bt eval    │
  │ prompt  │   │   PR --first N; merge full golden + m3 if LLM-judge     │
  │ schema  │   │   push_prompt + tag staging; owners-only production     │
  │ suite   │   │   LD flag 5% sticky thread_id; MCP promote = HITL       │
  └─────────┘   └──────────────────────────┬──────────────────────────────┘
                                           ▼
                    ┌──────────────────────────────────────────────────────┐
                    │ DATA: flag SDK → bundle → Luna  (judge OFF p99)      │
                    │   Hub tag is a POINTER; serving logs the tuple       │
                    │   prompt-only: FLAG, not a new Agent Server revision │
                    │   cache key includes prompt_hash (03 bust on promote)│
                    └──────────────────────────────────────────────────────┘
```

**Trade-off matrix:**

| Axis | **A Playground Hub as SoT** | **B Git SoT + Hub tags + flags (recommended)** | **C Full SageMaker/Vertex for prompts-only** |
| --- | --- | --- | --- |
| **Cost** | CI **$0.88–$21.13 / 1k** still owed if you gate; Hub seats; drift incidents are the real $ | Same CI **[inferred]**; LS **$39/seat** (8 eng **$312/mo**) vs Braintrust Pro **$249**; 5% same-SKU canary **~$0** extra on **$880/mo** Luna 1M baseline | Registry + endpoint $ for **pointers**; minutes-class promote TTL; no extra quality vs B |
| **Latency** | Hub pull on the request; client cache **> ⚠️ Gap ~5 min unverified** | Flag SDK **p50 1 / p95 10 / p99 50 ms [inferred]** on top of 16 extract **1,200 / 4,000 / 12,000 ms**; MLflow alias **60,000 ms** | Endpoint update **minutes-class [inferred]** — wrong clock for a prompt |
| **Ops** | Webhook → CI; owners-only or anyone with `prompts:tag` ships | CODEOWNERS + required check; dual-write Hub from git; cassette cache | ModelPackageGroup for a YAML file is ceremony |
| **Security** | Playground save = prod if `:production` or `latest` | Owners-only + MCP HITL; PII before goldens; WORM of hashes | IAM yes; still need git for `schema_hash` |
| **Scalability** | One webhook/workspace; ingest **5k POST/min** | Eval TPM bulkhead vs user; `--first N` on PR | Endpoint autoscaling unused for prompt-only |

**Decision.** **B wins.** Git is SoT; Hub is the runtime pointer; CI is fail-closed; 5% canary via flags; prompt-only does **not** create an Agent Server revision. **A** is silent drift. **C** is the wrong primitive: SageMaker approval is for packages, not weekly prompt hashes. I would not serve `latest`. I would not promote from `--first 20`.

#### Scenario 2 — Fine-tuned + base-model fleet

**Problem.** LoRA/SFT on UC / SageMaker / Vertex; base model remains an API SKU; retrieval index must stay compatible (15). Wrong LoRA answers must not ship. Shadow then A/B. Someone will propose “just canary the new adapter like a prompt.” Someone else will buy Bedrock PT and call it a registry.

**Proposed architecture (recommended B):**

```
  ┌─────────┐   ┌─────────────────────────────────────────────────────────┐
  │ train   │──▶│ CONTROL: register weights @challenger (not stage=Prod)  │
  │ LoRA    │   │   copy_model_version staging→prod catalogs              │
  │ git     │   │   5–20% SHADOW (user sees base) → nDCG/task offline     │
  │ prompt  │   │   then sticky A/B + CUPED; flag kill; PT after approve  │
  │ index   │   │   bundle: new model_id + SAME prompt_hash unless SFT    │
  └─────────┘   │   assumed a new template (then TWO changes — don't mix) │
                └──────────────────────────┬──────────────────────────────┘
                                           ▼
                    ┌──────────────────────────────────────────────────────┐
                    │ DATA: user → base  |  sidecar → LoRA (shadow)        │
                    │   dual index_gen if embed model moved (15)           │
                    │   Bedrock PT/Reserved = CAPACITY for chosen model_id │
                    └──────────────────────────────────────────────────────┘
```

**Trade-off matrix:**

| Axis | **A Prompt-only canary on a new LoRA (wrong)** | **B Registry aliases + 5% shadow then canary (recommended)** | **C 100% shadow forever** |
| --- | --- | --- | --- |
| **Cost** | Looks free; ships wrong weights. Canary extra **~$0** only if SKU matches — a LoRA is **not** a prompt | Shadow 5% **+$44/mo** on **$880** Luna baseline **[inferred]**; then 5% 2× SKU **+$44**; training GPU extra; PT hourly whether used | **+$880/mo** forever **[inferred]**; Flex/Batch can cut eval $ but not this 2× |
| **Latency** | User sees LoRA immediately; p99 includes bad answers | Shadow latency on the **sidecar**, not the user, if implemented correctly | Sidecar always-on 2× TPM |
| **Ops** | One flag | Two model IDs, IAM, `copy_model_version`, keep prior package, `index_gen` dual-run | Simple and wasteful |
| **Security** | No approval gate | IAM / UC / protected aliases; MCP promote HITL; weight exfil is the new surface | Same weights exposure, no graduation |
| **Scalability** | Confounds prompt vs weight | Champion/challenger aliases exist **because** two hashes/IDs must be live | Doubles serving forever |

**Decision.** **B wins.** Register the LoRA; `@challenger`; **shadow** 5–20% with user still on base; score nDCG/task offline; then sticky A/B with CUPED; sequential/mSPRT if you must peek; Miller N from golden, not from 1% of low traffic. **A** confounds two changes and puts unproven weights on users. **C** is a permanent **2×** bill. Bedrock PT is **capacity** after registry approve — still not the registry. HF Hub may **publish** open weights at a SHA; enterprise serving still pins `revision`. If `index_gen` moves, dual-index + shadow nDCG + alias flip (15) — not a prompt hotfix.

---

## Common Failure Modes

| Failure | Mechanism | Detection | Mitigation |
| --- | --- | --- | --- |
| **Silent prompt drift** | Playground / Hub save; git unchanged; app pulls `:production` or `latest` | Served `prompt_hash` ≠ git main; webhook without CI | Git SoT or owners-only + webhook-gated CI; never pull `latest` in prod |
| **Eval-set overfitting** | Prompt compiled against the gate set (16 judge-as-loss / DSPy metric = loss) | Hold-out / sealed split drop; online ≠ CI | Separate **optimize** vs **gate** suites (04/16); freeze gate `eval_suite_id` |
| **A/B peeking** | Dashboard p<0.05 early stop | Inflated ship rate | Always-valid / mSPRT (Johari); pre-registered N or sequential design |
| **Judge-as-loss** | Shipping the prompt that maximizes the judge (04/16) | Human spot-check; pairwise | Hard oracles on CI; judge only soft; majority-of-3 **CI only** |
| **Canary too small** | 1% of low-traffic product; N ≪ 969 | Wide CI; flip-flops | Don’t promote on canary “green”; blast-radius limiter; power from golden + CUPED |
| **50-item 3 pp claim** | Smoke suite as ship proof | Miller: n ≈ 969 | `--first N` is non-final; powered gate **~$2.25 / ~$20.50 [inferred]** |
| **`model_id` change without re-embed** | New chat model; same vectors; or new **embedding** `model_id` with old index (15) | nDCG cliff; dim mismatch 4xx | `index_gen` in the bundle; dual-index + shadow nDCG + alias flip (15) |
| **`schema_hash` mismatch** | Hub `response_format` ≠ deployed GBNF | `parse_ok` drop; 400s | CI equality; decoder constraint in the same PR as prompt (16) |
| **Registry pointing at deleted artifacts** | Alias → deleted version; UC file gone; HF tag moved; Vertex **default auto-moves** | Sudden 404 / silent default swap | Pin version/SHA in the release record; GC retains last N prod versions |
| **Extended-trace surprise bill** | Online evaluator retention upgrade; experiments extended-by-default | Invoice **.50¢** vs **.05¢** | Opt out per evaluator; spend limits |
| **Control-plane 429** | LangSmith **5k POST/min**; **500k events/hr**; W&B Weave ingest | Eval CI red, prod still up | Batch SDK; sample online eval; cassette cache |
| **Bedrock PT as “registry”** | Team thinks MU purchase versioned the model | Wrong `model_id` still serving | PT/Reserved = **capacity**; version in SageMaker/git |
| **Mid-thread promote** | Alias TTL expires; next turn new hash | Personality shift | Sticky `thread_id`; drain |
| **OpenAI Evals hard outage of the gate** | Read-only **2026-10-31**, shutdown **2026-11-30** | Merges fail closed; ChatGPT still works | Promptfoo / pytest-langsmith / Braintrust in git **before** that date |
| **Braintrust green on exceptions-only** | Default reporter ignores score drop | Quality cliff ships | Custom `Reporter.reportRun` |
| **Judge on user p99** | Online m3 on the request | p99 → 16 extract SLO blown | Sidecar; coverage%; this module must not inflate **1,200 / 4,000 / 12,000 ms** |
| **Skip merge gate on judge 429** | “CI flake, merge anyway” | Silent quality drop | Circuit open → **fail the job**; skip score only on **online** sidecar |
| **MCP promote from the model** | Tool descriptions in context | Production alias rewritten | Gateway PEP; assistants `eval.run` staging only |
| **Cache serve old completion** | App cache omits `prompt_hash` | Stale answers after promote | Key `(model_id, prompt_hash, schema_hash, decoding_params)`; expect write stampede (03) |
| **MLflow stages in 2026** | `models:/name/Production` | Deprecated since ~2.9 | `@champion` / `@challenger` |
| **100% shadow forever** | Fear of cutover | **+$880/mo** on 1M Luna | Graduate or kill; shadow is a phase |
| **DSPy compile as online path** | Teleprompter on the request | p99 / $ | Offline job; this module promotes `program.json` (16) |
| **PII golden in LangSmith** | Raw prod trace → dataset | Indefinite retention | Detect→redact→audit **before** add; GDPR deletes blob **and** digest policy |

No public post-mortem corpus beyond vendor deprecations and SDK issues (`#2126`, Evals sunset). Do not invent incidents.

---

## Key Takeaways

- This layer is the **promotion control plane** (git + CI + registry + flags), not Agent Server internals (13), not DSPy/MIPRO (16), not judge math (04). Pin **`(model_id, prompt_hash, schema_hash, decoding_params, index_gen, eval_suite_id)`**. Env tags are **mutable pointers**. Serving `latest` is not a release. Bedrock PT is **capacity**. MLflow stages → **`@champion`**.
- Canonical path: author in git → CI fail-closed golden (m3 on **ship** only, 04) → register immutable version → staging alias → 5% sticky canary → promote (flag 100%) → observe sidecar (05). Rollback = **flag first**. LangSmith Deployment **revision** is one target; prompt-only uses **flags in front of the graph**.
- A **run** is one execution of a pinned bundle; an **experiment** is the matrix; a **release** is the alias move. Hub **commit vs tag**. Git SoT vs playground drift. Vertex `default` auto-moves on delete. HF pin **full SHA**.
- A/B: sticky hash; shadow = **2×** $ on the slice; CUPED ~**50%** Bing (Deng); peeking → **mSPRT** (Johari); Miller **n ≈ 969** for 3 pp — **50-item smoke cannot underwrite**. Canary is blast-radius, not the proof.
- **$ / 1k CI [inferred]:** Luna only **$0.88**; +Luna judge **$1.36**; Luna+Luna m3 **$2.32**; Luna+Sonnet m3 **$21.13**. Miller ~**$2.25** / **$20.50**. 1k extended LangSmith experiment **~$5** platform. Canary 1M Luna **$880/mo**: 5% same-SKU **~$0**; 5% 2× **+$44**; 5% shadow **+$44**; 100% shadow **+$880**. LS Plus **$39/seat**; Braintrust Pro **$249**; W&B Pro from **$60**; Engine **$7.50–$45**/cycle **[inferred]**.
- **Two clocks [inferred ms]:** user flag SDK **1 / 10 / 50** on top of 16 extract **1,200 / 4,000 / 12,000** — judge OFF. CI 1k Luna-m3 **180,000 / 600,000 / 1,800,000**. PR smoke **30,000–60,000**. `/ok` **30,000** is health. Promote: flag **seconds**; MLflow alias **60,000**; endpoints **minutes-class**. RPO = last merge. RTO flag **seconds** vs image **~20 min**.
- Zero-Trust MCP is **in this file**: `eval.run` / `registry.set_alias` / `promote_tag`. OAuth 2.1, RFC 8707, **no** Hub/MLflow token passthrough, hash-pin. Assistants: `eval.run` **staging only**. Promote = human + break-glass. MCP is not the registry PEP. PII: **detect → redact → audit** before goldens enter git/Hub. OpenAI Evals dead **2026-11-30**.

---

## Interview Q&A

**Q1. What is this layer, in one minute?**  
I treat LLMOps as a **promotion control plane**, not a playground. Control owns git, CI, the registry, aliases, and flags. Data owns live inference with the judge **off** user p99. Persistence is artifacts, experiments, eval snapshots, Hub commits. Tool proxies are registry APIs plus MCP promote tools behind a gateway. Telemetry is experiment id, canary task-metric, alias audit. I pin `(model_id, prompt_hash, schema_hash, decoding_params, index_gen, eval_suite_id)`. Tags are pointers. `latest` is not a release. Agent Server is one deploy target (13); DSPy compile is an offline job I promote (16); Miller n and m3 live in 04 — I decide **when the gate blocks merge**.

**Q2. Walk a change through your diagram.**  
Author in git. PR smoke `--first N`. Merge: full golden, fail-closed, majority-of-3 if the metric is an LLM-judge. Register an immutable version; do not retag production. Stage alias / `@challenger`. Flag 5% sticky on `thread_id`. Promote with CAS (who, prev hash, new hash, experiment id) plus flag 100%. Observe sidecar traces (05). Kill = flag 0%. I would not rebuild Agent Server to change a prompt tag. I would not put the judge on the request.

**Q3. MLflow stages vs aliases vs Bedrock PT vs Vertex default.**  
Stages None/Staging/Production/Archived are **deprecated** since ~2.9; I use `models:/name@champion` and `@challenger` so two versions can run. UC has no stages; Champion/Challenger aliases plus catalog copy. W&B protected aliases are Admin-only. HF I pin the **40-char SHA**. SageMaker is `PendingManualApproval` → `Approved`. Vertex `default` **auto-moves** if you delete that version — a footgun. Bedrock PT is **capacity**, not a registry. GitLab Model Registry is semver + MLflow client, 5 GB/file.

**Q4. What is a run vs an experiment vs a release?**  
A run is one execution of a pinned bundle on one example (offline) or one live request (online). LangSmith: experiment = app version × dataset; each example is a run with `reference_example_id`; online runs have no reference. Weave: `Evaluation` is a blueprint; `.evaluate()` is a run. MLflow traces are not yesterday’s `.pkl` run. The release is the **alias move** after the experiment clears the gate. Hub commit is immutable; Hub tag is a pointer.

**Q5. Give me `$ per 1k` for the CI loop and the canary.**  
Stated loop: Luna **$0.20 / $1.20 per 1M**, Sonnet 4.6 **$3 / $15**, task **2,000/400**, judge **1,500/150**. **[inferred]** Luna only **$0.88 / 1k**; Luna+Luna judge **$1.36**; Luna+Luna m3 **$2.32**; Luna+Sonnet m3 **$21.13**; +10% judge flake **$2.46 / $23.16**. Miller n≈969 ≈ **$2.25** or **$20.50**. 50-item smoke **cannot** underwrite 3 pp. LangSmith 1k extended experiment **~$5** platform. 1M req/mo Luna baseline **$880/mo**: 5% same-SKU **~$0**; 5% 2× SKU **+$44**; 5% shadow **+$44**; 100% shadow **+$880**. Plus **$39/seat**, Braintrust Pro **$249** (scores **$1.50/1k** overage), W&B Pro from **$60**, Engine **$7.50–$45**/cycle **[inferred]**.

**Q6. Two clocks — what p50/p95/p99 do you actually quote?**  
Nobody publishes eval-job percentiles (`> ⚠️ Gap`). User path: flag SDK **[inferred] p50 1 / p95 10 / p99 50 ms** on top of 16 extraction **1,200 / 4,000 / 12,000 ms** — I will not inflate that with a judge. CI 1k Luna-m3 **[inferred policy] p50 180,000 / p95 600,000 / p99 1,800,000 ms**. PR smoke **30,000–60,000 ms**. LangGraph `/ok` **30,000 ms** is health, not eval SLO. Promote TTL: flags **seconds**, MLflow alias **60,000 ms**, Vertex/SageMaker **minutes-class [inferred]**. Rollback flag first. Hub client ~5 min is **unverified**.

**Q7. Miller, CUPED, peeking — and when does the gate block?**  
I cite 04: n≈969 independent questions for 3 pp at α=0.05, β=0.20, σ²≈1/9; majority-of-3 on **CI ship**; flake retries on idempotent score HTTP vs a real metric drop. This module **blocks merge/promote** when the versioned golden suite fails or the judge circuit is open. I do not skip the gate. Online I skip the score and decrement coverage%. CUPED ~50% variance cut at Bing; needs Y_pre. Peeking without mSPRT inflates Type I. A 5% canary on a low-traffic product is underpowered — it is a blast-radius limiter.

**Q8. Circuit breaker and fallback.**  
Independent breakers on CI judge API and registry API, closed → open → half-open. Judge outage: **fail the CI job** — never skip merge. Online sidecar: skip score, coverage%. Registry outage: **last-known bundle**, fail-open flags. Canary kill = flag 0%. Dual hashes sticky on `thread_id`. Fallback chain: last-known bundle → prior `@champion` → previous revision/endpoint. I would not serve `latest` as a fallback.

**Q9. Zero-Trust MCP on promote — module 19 does not exist.**  
I put the PEP **here**. Tools: `eval.run`, `registry.set_alias`, `promote_tag`. Gateway: OAuth 2.1, RFC 8707 resource = this MCP server, **no** token passthrough to Hub or MLflow (RFC 8693 exchange), hash-pin tool JSON **and** server digest, audience-bound tokens. Identity from RunContext, not JSON `actor`. Assistants get `eval.run` on **staging** only. Promote is human + break-glass. MCP is **not** the registry PEP — IAM, UC, Owners-only, protected aliases are. CVE-2025-54136 if I skip re-hash; CVE-2025-6514 if I `open()` hostile auth metadata.

**Q10. Cache and `index_gen` on promote.**  
New `prompt_hash` busts prefix cache (03): compulsory miss, Anthropic/OpenAI write-rate 1.25×/2×, stampede writes. App cache key **must** include `(model_id, prompt_hash, schema_hash, decoding_params)` or it serves the old completion. MLflow alias cache **60 s**. If I change embedding `model_id` without rebuilding, that is a 15 incident: `index_gen` in the bundle, dual-index, shadow nDCG, alias flip — not a prompt hotfix.

**Q11. RPO/RTO and OpenAI Evals sunset.**  
RPO = last git merge + immutable version. RTO = flag 0% in **seconds** vs image rebuild **~20 min**. Control-plane outage: last bundle. CI red / prod green is correct. Product SLO is model API + app; Bedrock Reserved **99.5%** is capacity. Hosted Evals read-only **2026-10-31**, shutdown **2026-11-30** — if CI still calls them, merges fail closed while chat works. I migrate the gate to Promptfoo / pytest-langsmith / Braintrust in git. LangSmith goldens are indefinite retention; GDPR deletes the example **and** the digest policy.

**Q12. Two designs in 90 seconds.**  
Prompt-only SaaS: **B** git SoT + Hub tags + owners-only + CI golden + 5% flag canary. Not playground-as-SoT. Not SageMaker for YAML. Fine-tune fleet: **B** registry aliases, 5% shadow then canary, same `prompt_hash` unless the SFT assumed a new template. Not prompt-canary on a LoRA. Not 100% shadow forever. Not Bedrock PT as a registry.

---

## Key Numbers to Memorize

### Invariant / registries / gates
| Number | What |
| --- | --- |
| **`(model_id, prompt_hash, schema_hash, decoding_params, index_gen, eval_suite_id)`** | Release pin; change ⇒ new release; tags are pointers |
| **`@champion` / `@challenger`** | MLflow aliases; stages **deprecated** ~2.9 |
| **40-char SHA** | HF production pin; 7-char / `main` are not |
| **`default` auto-move** | Vertex footgun on delete of the default version |
| **PT ≠ registry** | Bedrock MU/Reserved = **capacity**; Flex for evals |
| **n ≈ 969 / 3 pp** | Miller (04) — this file **blocks merge** below power; 50-item smoke cannot underwrite |
| **majority-of-3** | CI **ship** gates only (04); not user p99 |
| **CUPED ~50% / 15.5% vs 1.3%** | Bing Deng; 2026 platform CUPED vs LLM-pred extra |
| **mSPRT / peeking** | Johari always-valid; dashboard p<0.05 is invalid |
| **2026-11-30 / 2026-10-31** | OpenAI hosted Evals shutdown / read-only |
| **2026-06-23** | UC model-lifecycle docs (aliases, `copy_model_version`) |

### $ **[inferred]** where marked
| Number | What |
| --- | --- |
| **$0.20 / $1.20 per 1M** | GPT-5.6 Luna in/out (16 index rates) |
| **$3 / $15 per 1M** | Sonnet 4.6 in/out |
| **2,000 / 400 ; 1,500 / 150** | Task in/out ; judge in/out |
| **[inferred] $0.88 / $1.36 / $2.32 / $21.13** | Luna / +judge / Luna-m3 / Sonnet-m3 per 1k CI tasks |
| **[inferred] $2.46 / $23.16** | +10% judge flake retries |
| **[inferred] $2.25 / $20.50** | Miller n≈969 Luna-m3 / Sonnet-m3 |
| **[inferred] ~$0.04–$1.06** | 50-item smoke — **not** a 3 pp proof |
| **[inferred] $880/mo** | 1M req Luna 2k/400 baseline |
| **~$0 / +$44 / +$44 / +$880** | 5% same-SKU / 5% 2× / 5% shadow / 100% shadow |
| **[inferred] ~$5** | 1k LangSmith **extended** traces (.50¢ × 1000) |
| **$39 / .05¢ / .50¢** | LangSmith Plus seat; base / extended per trace |
| **$249 / $1.50/1k** | Braintrust Pro; score overage |
| **from $60** | W&B Pro |
| **[inferred] $7.50–$45** | LangSmith Engine cycle ($1.50 × 5–30 LCU) |
| **$312 vs $249** | 8 × LS Plus vs Braintrust Pro **[inferred arithmetic]** |
| **> ⚠️ Gap** | UC $ per registered version — do not invent |

### Latency / availability / security (numeric ms)
| Number | What |
| --- | --- |
| **1 / 10 / 50 ms** | **[inferred]** flag SDK p50 / p95 / p99 on the **user** clock |
| **1,200 / 4,000 / 12,000 ms** | 16 extraction **[inferred]** — this module must **not** inflate |
| **180,000 / 600,000 / 1,800,000 ms** | **[inferred policy]** CI 1k Luna-m3 p50 / p95 / p99 (3 / 10 / 30 min) |
| **30,000–60,000 ms** | PR smoke `--first N` p50 **[inferred]** |
| **30,000 ms** | LangGraph `/ok` poll — **health**, not eval SLO |
| **60,000 ms / 60 s** | MLflow alias prompt cache TTL |
| **seconds / minutes-class / ~20 min** | Flag RTO / endpoint promote **[inferred]** / image rebuild |
| **99.5%** | Bedrock Reserved **capacity** target — not a registry SLO |
| **5k POST /runs/min ; 500k events/hr** | LangSmith ingest |
| **RFC 8707 / RFC 8693** | MCP resource indicator / **no** token passthrough |
| **8.8 / 9.6** | CVE-2025-54136 MCPoison / CVE-2025-6514 connect-time RCE |
| **indefinite** | LangSmith dataset retention once added |
| **detect → redact → audit** | **Before** goldens enter git/Hub; GDPR deletes blob **and** digest |
| **> ⚠️ Gap** | Eval-job vendor p99; Hub client cache ~5 min **unverified** |

**Dates:** research frozen **2026-09-03** (62 sources). Do not treat inferred `$` or ms as list prices or vendor SLOs. Do not treat Bedrock PT as a model registry. Do not treat `latest` as a release.
