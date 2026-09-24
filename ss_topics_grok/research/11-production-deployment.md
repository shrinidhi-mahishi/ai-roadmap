# Research: Production Deployment

**Date researched**: 2026-09-23
**Sources consulted**: 100

Scope: the **ops/runtime plane** — **Docker** (multi-stage images, GPU vs CPU, secrets not in layers, distroless, healthchecks), **CI/CD** (eval gates from [`10-evals-observability.md`](10-evals-observability.md), canary, blue/green, prompt-as-code, model SKU pinning), **guardrails** (input/output filters, NeMo / Presidio / Llama Guard, schema, allowlists, jailbreak), **cost tracking** (token meters, per-tenant budgets, OpenTelemetry `gen_ai.*`, spend caps, 429 spend), **fallback chains** (primary → secondary vendor → deterministic; model routing), **checkpoint and resume** (LangGraph checkpointer, Temporal, saga, exactly-once tools). LangGraph durability modes, Agent Server leases, and Temporal plugin semantics live in [`05-langgraph-state-machines.md`](05-langgraph-state-machines.md) — this file cites them, it does **not** recopy Pregel / reducer / `interrupt()` tables. Offline vs online eval clocks, promptfoo / Braintrust CI, McNemar, and canary sampling live in 10. Model list prices used in §2 are from [`01-python-llm-foundations.md`](01-python-llm-foundations.md) (2026-09-23). Zero-Trust MCP egress (OAuth 2.1, RFC 8707, tool allowlists) lives in [`08-mcp-integrations.md`](08-mcp-integrations.md). GPU Operator / vLLM / GIE inference-cluster topology is adjacent (`research_cursor_grok_4.6/research/16-production.md`) and is referenced only where the serving data plane collides with this ops plane. ⚠️ No unpublished production p50/p95/p99 for Agent Server, NeMo rails, or Temporal Activity latency is invented. `$ per 1k production requests` figures are **[inferred]** from named SKUs × a stated mix — not a vendor “per request” SKU.

Invariant: **the model never deploys, never gates a canary, never opens a circuit, never meters spend, and never commits a checkpoint.** CI and GitOps own *what may run*. The serving data plane owns *tokens in flight*. The guardrail sidecar is a **policy enforcement point (PEP)** on input, output, and tool args. The checkpoint / Temporal store owns *resume identity*. Collapsing those four — baking API keys into image layers, scaling GPU replicas on CPU HPA, promoting a prompt because HTTP 200 looked green, retrying a spend-cap 429 into a second vendor until the card is empty — is the dominant Principal-Architect failure on this plane.

---

## 1. System Topology & Mechanics

### 1.1 Four planes, four clocks

| Plane | What it is | Clock | Typical store | Failure if mixed |
| --- | --- | --- | --- | --- |
| **CI / GitOps (control)** | Image build, Cosign+SBOM, prompt commit SHA, pinned model snapshot, eval gate from 10, Argo Rollout weights | Wall-clock of the pipeline; fail-closed | Git + OCI registry + experiment artifacts | Shipping because `/health` is 200 while the golden set regresses |
| **Serving data** | Agent Server / FastAPI workers, LiteLLM/Portkey gateway, SSE streams, optional GPU NIM/vLLM | User SLO: TTFT / e2e | In-flight TCP; KV on GPU if self-host | Treating a 12-minute decode as a 30s HTTP request; CPU HPA on an idle-waiting agent |
| **Guardrail sidecar (PEP)** | NeMo IORails / Llama Guard / Presidio / schema / tool allowlist | Added to TTFT if inline; must be bounded or fail-open with audit | Policy config (`GuardrailConfig`, `config.yml`), classifier weights | Synchronous second LLM on every token; fail-open with no log |
| **Checkpoint / workflow store** | LangGraph Postgres/Mongo checkpointer; Temporal event history; Kafka/SQS for async tools | Durable-execution clock (super-step / Activity / offset) | Postgres (`DATABASE_URI`); Temporal persistence; Redis **ephemeral** on Agent Server | Replaying a tool `payments.charge` because the node restarted |

LangSmith Deployment (formerly LangGraph Platform) productizes the split: **control plane never connects to the data plane**; a **listener polls** control-plane APIs. Cloud = LangChain hosts both; Hybrid/BYOC = control at LangChain, Agent Servers + Postgres + Redis in your VPC; Standalone Agent Server = you host the data plane with **no** control plane ([Deployment](https://docs.langchain.com/langsmith/deployment); [standalone server](https://docs.langchain.com/langsmith/deploy-standalone-server); 05 §1.12). Standalone is production-ready for “lightweight,” but **do not run it serverless**: scale-to-zero can lose tasks; Helm on Kubernetes is the path LangChain regularly tests (independent queue autoscaling, graceful run draining, `queue.enabled` split) ([standalone server](https://docs.langchain.com/langsmith/deploy-standalone-server)).

Agent Server resources: **assistants** (compiled graph + config), **threads** (checkpointed state), **runs**, **crons**. Persistence: core rows + checkpoints + store in **Postgres**; **Redis is pub/sub + queue signaling — no user data**. Queue: **at most one run per `thread_id`**. `N_JOBS_PER_WORKER` default **10**. Durability on runs: `async` (default), `sync`, `exit`; `checkpoint_during` is **deprecated** (05). Double-texting: `enqueue` / `reject` (HTTP **409**) / `interrupt` / `rollback` ([Agent Server](https://docs.langchain.com/langsmith/agent-server)). Server **replaces** any checkpointer you compiled. Cloud payload max **25 MB** → **413**. Health: `GET /ok` → `{"ok":true}` — and `http.disable_meta` still leaves `/ok` ([CLI](https://docs.langchain.com/langsmith/cli); [standalone](https://docs.langchain.com/langsmith/deploy-standalone-server)).

Interview move: **CI is not serving, and `/ok` is not quality.** Topology that lets the scheduler SIGTERM a replica without draining the current super-step (05: `RunControl.request_drain()`, Agent Server already drains at super-step boundaries) treats durable work as cattle.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ CI CONTROL  (GitHub Actions / Argo CD)                                       │
│  Cosign+SBOM → prompt SHA + model snapshot → promptfoo/Braintrust gate (10)  │
│  → image digest → Helm/Rollout. MODEL NEVER MERGES.                          │
└───────────────┬──────────────────────────────────────────────────────────────┘
                │ signed digest + env from Secret (not layers)
                ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ EDGE / GATEWAY  (LiteLLM / Portkey / Envoy)                                  │
│  AuthZ, tenant budget, SKU alias, canary weight, circuit, fallback chain     │
└───────┬───────────────────────────┬──────────────────────────────────────────┘
        │                           │
        ▼                           ▼
┌───────────────────┐    ┌─────────────────────────────────────────────────────┐
│ GUARDRAIL PEP     │    │ SERVING DATA  (Agent Server / worker)               │
│ NeMo / Presidio / │    │  Graph nodes, SSE, tool HTTP. Clock = user SLO.     │
│ Llama Guard /     │    │  GPU NIM only if self-host weights.                 │
│ schema+allowlist  │    └──────────────────┬──────────────────────────────────┘
└───────────────────┘                       │ put / Activity result
                                            ▼
                          ┌────────────────────────────────────────────────────┐
                          │ CHECKPOINT STORE  Postgres / Temporal / Kafka DLQ  │
                          │  thread_id + checkpoint_id / WorkflowId            │
                          └────────────────────────────────────────────────────┘
```

### 1.2 Docker: multi-stage, GPU vs CPU, secrets, distroless, healthchecks

**Two images, two blast radii.** The **agent/Temporal worker** is a CPU process: Python, HTTP, checkpointer client. The **inference engine** (optional) is a GPU process: NIM/vLLM, HBM, CUDA userspace. Mixing them in one container couples image size, CVE lag, and SIGTERM semantics. LangGraph’s production packaging is `langgraph build -t my-image` from `langgraph.json`; `langgraph dockerfile` emits a Dockerfile you can harden; `langgraph deploy` builds, pushes, and updates a Cloud deployment (beta; remote build if Docker is missing). Pricing transition: `--deployment-type serverless|dedicated` on new LCU/LSU billing; orgs on previous pricing until **2026-10-01** still pass `dev`/`prod` ([CLI](https://docs.langchain.com/langsmith/cli); [quickstart](https://docs.langchain.com/langsmith/deployment-quickstart); 05 §2.4).

**`langgraph.json` is executable config.** `env: ".env"` vs inline `"OPENAI_API_KEY": "sk-..."` — docs: **do not commit API keys in `langgraph.json`** ([configuration](https://langchain-ai-langgraph-40.mintlify.app/cli/configuration)). `dockerfile_lines` appends `RUN` after the parent LangGraph API image (e.g. `libjpeg` for Pillow) ([custom Docker](https://docs.langchain.com/langsmith/custom-docker)). Graphs load **once** at container start unless you export a factory (05).

**Multi-stage (CPU agent).** Pattern that survives review: builder (`python:3.13` / `uv sync --locked --no-dev --no-editable`) → runtime `gcr.io/distroless/python3-debian12:nonroot` or `gcr.io/distroless/cc-debian12:nonroot` with a copied venv + interpreter. kagent’s production Dockerfile: pin **by digest** (`distroless/cc-debian12:nonroot@sha256:…`) because distroless has no versioned tags beyond `:nonroot`/`:latest`/`:debug`; `USER 65532`; pre-create writable dirs because distroless **cannot mkdir at start**; agents that need bash/sandbox use a **full** image, not distroless ([kagent Dockerfile](https://github.com/kagent-dev/kagent/blob/74321ee6/python/Dockerfile); [uv Docker](https://github.com/astral-sh/uv/blob/2318e48e/docs/guides/integration/docker.md); [distroless](https://github.com/GoogleContainerTools/distroless)). Google’s `python3-debian12` is Python **3.11**, amd64/arm64, **no shell**, **no ctypes** on the default image (plus/debug variants differ) ([distroless PR #1415](https://github.com/GoogleContainerTools/distroless/pull/1415); [issue #1409](https://github.com/GoogleContainerTools/distroless/issues/1409)). `CMD` must be exec-form arrays. NVIDIA publishes a separate **Python distroless** on NGC (`nvcr.io/nvidia/distroless/python:3.12-v4.0.3`, ~56 MB compressed, signed) — still **not** a CUDA inference runtime ([NGC](https://catalog.ngc.nvidia.com/orgs/nvidia/distroless/containers/python/3.12-v4.0.3)).

**GPU images are not “CUDA + app.”** Three layers (same as 16-production, restated for this plane): (1) **host driver** (GPU Operator DaemonSets); (2) **Container Toolkit / CDI** injecting devices; (3) **app image** (NIM/vLLM). The app image must **not** ship a second driver. Distroless Python **does not** contain `libcuda` / cuDNN / NCCL — do not copy a vLLM wheel onto `python3-debian12` and expect kernels. CUDA userspace stays on a patched NVIDIA/NIM base; **agent workers stay distroless**. ⚠️ NVIDIA CUDA bases lag distro OpenSSL patches; inheriting `nvidia/cuda:*-ubuntu*` as *runtime* is a CVE-lag decision.

**Secrets never in layers.** `ARG`/`ENV` persist in image history even in discarded stages. BuildKit `RUN --mount=type=secret,id=…` mounts `/run/secrets/<id>` for **that RUN only** — not in layers, not in cache blobs of the secret file ([Docker secrets](https://docs.docker.com/build/building/secrets/); [BuildKit pattern](https://oneuptime.com/blog/post/2026-02-08-how-to-use-run-mounttypesecret-for-build-time-secrets/view)). Runtime keys: Kubernetes Secret volumes / CSI / External Secrets, `docker run --env-file` (standalone docs), **not** `langgraph.json` inline, **not** `COPY .env`. Forum/05: bearer tokens in graph `state` land in checkpoints — use `Runtime.context` / `UntrackedValue` (05 §1.2).

**Healthchecks: Docker vs Kubernetes are different APIs.** Compose healthchecks in LangChain’s standalone example: Redis `redis-cli ping` interval 5s retries 5; Postgres `pg_isready` `start_period: 10s`; Mongo replica-set initiate in the probe; API `depends_on: condition: service_healthy` ([standalone](https://docs.langchain.com/langsmith/deploy-standalone-server)). **Kubernetes ignores Docker `HEALTHCHECK`.** Use probes:

| Workload | Liveness | Readiness | Startup |
| --- | --- | --- | --- |
| Agent Server | `/ok` (process up) | `/ok` + Postgres reachable **or** a custom “queue worker listening” | short; image is small |
| NIM / vLLM | `/v1/health/live` (proxy up, **no** backend) | `/v1/health/ready` (weights in HBM) | same as ready; Operator default **failureThreshold 120 × period 10s = 20 min** ([NIM Operator](https://docs.nvidia.com/nim-operator/latest/service.html); [NIM architecture](https://docs.nvidia.com/nim/large-language-models/2.0.3/reference/architecture.html)) |
| Guardrail classifier | process | model loaded | load time of Llama Guard 4 12B ≠ Prompt Guard 2 BERT |

Inverting live/ready on NIM sends traffic to a loading GPU and then OOM-kills it. `start_period` on Compose ≠ K8s `startupProbe`. Probe **timeouts do not govern in-flight SSE** — idle timeouts on the proxy do (§5). Distroless runtimes often **lack `curl`/`wget`**: do not copy a `HEALTHCHECK CMD curl` from a slim tutorial; use a Kubernetes HTTP probe against `/ok` or ship a static probe binary in the image. Compose `depends_on: condition: service_healthy` is the local analog of readiness: Agent Server must not start serving runs until Redis and Postgres have passed `redis-cli ping` / `pg_isready` ([standalone](https://docs.langchain.com/langsmith/deploy-standalone-server)). Dual-stack: Agent Server listens IPv4+IPv6 as of **0.14.0**; pin `LANGGRAPH_SERVER_HOST` to `0.0.0.0` or `::` if the mesh is single-family.

**GPU vs CPU resource requests.** CPU agent: request/limit on millicores + RAM (tokenizer + Python + checkpoint serde sit in DRAM). GPU engine: `nvidia.com/gpu` is an **integer**, not millicores; RAM still matters for tokenizer and Prometheus multiprocess dirs. Do not set a CPU HPA on the GPU Deployment. Do not give the distroless worker a GPU request “just in case” — you will bin-pack it onto expensive nodes and still not load weights.

**Streaming through ingress.** ingress-nginx default `proxy-read-timeout` **60s** (between successive reads, not whole response); `proxy-buffering` on by default. LLM SSE needs `proxy-buffering: "off"` (or `X-Accel-Buffering: no`) and a read timeout **above p99 silence** (docs examples 3600s) plus **SSE comments** (`: keepalive`) every 20–30s so Cloudflare-class ~100s idle closes do not 524 ([ingress-nginx ConfigMap](https://kubernetes.github.io/ingress-nginx/user-guide/nginx-configuration/configmap/); [kubernai streaming](https://kubernai.com/articles/streaming-ingress-for-llm-responses); [Envoy timeouts](https://www.envoyproxy.io/docs/envoy/latest/faq/configuration/timeouts.html)). Envoy **stream_idle_timeout default 5 minutes**; route `timeout` should be **0** for streams (it starts after the request is complete). GKE Inference Gateway: **streaming errors are not retried** (16-production). HPA on CPU during a stream sees the agent **waiting** — llm-d: GPU util can sit ~100% while batching regardless of queue; scale on EPP queue / running requests / inflight tokens, not CPU ([llm-d autoscaling](https://llm-d.ai/docs/dev/architecture/advanced/autoscaling); [KEDA+EPP](https://llm-d.ai/docs/dev/architecture/advanced/autoscaling/keda-epp)).

### 1.3 CI/CD: eval gates, canary, blue/green, prompt-as-code, SKU pinning

**Three artifacts, one release.** Code digest, **prompt commit**, **model snapshot**. A green pytest on the worker image with `gpt-x-latest` in env is not a release.

**Eval gate (cite 10, do not re-derive).** The **CI gate is fail-closed** on a pinned `(dataset id, tag, split)` with code oracles + calibrated judges. The **prod canary is fail-open** on sampled live traffic with a **coverage % SLO**. Mixing them — blocking merge on a 1% online sample, or shipping because pass@1 was green — is the topology error in 10 §1.1 / §1.6.

| Tool | What fails the job | Stochastic judges |
| --- | --- | --- |
| **promptfoo** `promptfoo/promptfoo-action@v1` | `fail-on-threshold` 0–100; or `jq .results.stats.failures` `>0`; Node **≥22.22**, **24 LTS** recommended; `repeat` + `repeat-min-pass` | Zero-failure is a **unit-test** gate (schema, PII regex). Too tight for LLM-as-judge (10) ([action](https://github.com/promptfoo/promptfoo-action/); [CI/CD](https://www.promptfoo.dev/docs/integrations/ci-cd/); [GitHub Action docs](https://www.promptfoo.dev/docs/integrations/github-action/)) |
| **Braintrust** `eval-action@v2` | Default exit ≠ quality: non-zero only if eval **throws**. Quality = `Reporter.reportRun → bool`. `--sample 20` is **non-final** | Smoke on PR; full dataset on merge (10) ([run in CI](https://www.braintrust.dev/docs/evaluate/run-in-ci)) |
| **LangSmith** | pytest wrappers; pin dataset tag; `num_repetitions` | Experiment artifacts outlive 14d traces (10) |

Cache `PROMPTFOO_CACHE_PATH` / `LANGSMITH_TEST_CACHE`: good for **scorer iteration**, poisonous if you think you re-measured the agent (10). Upload `results.json` + `report.html` so a deleted SaaS project does not erase the gate.

**Prompt-as-code.** LangSmith Hub: every push is a **commit hash**; pull `owner/name:COMMIT_SHA` (or a **commit tag** that you move after the gate). Default pull is **latest** — that is a footgun. You can `push` a `RunnableSequence` of prompt+model so the SKU travels with the prompt; `pull_prompt(..., include_model=True)` deserializes it. Public prompts: `dangerously_pull_public_prompt=True` only after review — manifests can set **custom base URL, headers, model name** (executable config, not text) ([manage prompts programmatically](https://docs.langchain.com/langsmith/manage-prompts-programmatically); [`pull_prompt`](https://reference.langchain.com/python/langsmith/client/Client/pull_prompt); [commit tags](https://docs.langchain.com/langsmith/manage-prompts)). Environments (staging/prod tags) + webhooks exist so GitOps can restart workers when `prod` moves — still **after** 10’s gate.

**Model SKU pinning.** From 01: GPT-5.4 snapshot `gpt-5.4-2026-03-05`; never `latest`. Gateway aliases (`smart-chat` → pinned snapshot) are the **control-plane** name; the body `model` field that hits the vendor is the **data-plane** name. Canary a new snapshot as a **new alias** with 10% HTTPRoute / LiteLLM weight, not by editing the alias in place.

**Argo Rollouts: two strategies, two cost models.**

| Strategy | Traffic | Analysis | LLM cost |
| --- | --- | --- | --- |
| **Canary** | `setWeight` steps; stable RS **kept at 100%** when traffic-managed (do not `dynamicStableScale` unless you can scale stable back) | Inline or background `AnalysisRun`; abort → weight 0 | Thin extra slice + judge/sidecar |
| **Blue/green** | `activeService` vs `previewService`; **atomic selector flip** | `prePromotionAnalysis` blocks the flip; `postPromotionAnalysis` can flip **back** | **Two full stacks** until `scaleDownDelaySeconds` (default **30s** — too short for GPU weight load) ([blue-green](https://argoproj.github.io/argo-rollouts/features/bluegreen/); [analysis](https://argoproj.github.io/argo-rollouts/features/analysis/)) |

`AnalysisTemplate` providers run **concurrently** inside one template; sequence with `initialDelay`. Job provider: container **exit 0/1** — this is how you wire 10’s eval harness or a canary judge **without** putting the judge on TTFT ([analysis overview](https://argoproj.github.io/argo-rollouts/features/analysis/)). `successfulRunHistoryLimit` / `unsuccessfulRunHistoryLimit` default **5** — export AnalysisRuns before reap. A canary that only watches HTTP 5xx **will promote a silent quality regression** (10). Gate on: golden Δ (CI already passed), live schema/PII rate, guardrail block rate (watch **false-positive**), spend per 1k, TTFT.

**GitOps vs Agent Server Cloud.** `langgraph deploy` updates in place by name. Self-host: Helm values pin image **digest**; Rollout owns pods. Do not let Studio “save prompt” bypass the SHA pin.

**Release pipeline (control-plane sequence).** Concrete order that respects 10’s two clocks:

1. PR: schema/PII promptfoo `FAILURES>0` (unit) + Braintrust `--sample 20` (non-final). No production MCP write tools (10: simulators in CI).
2. Merge: full golden tag + McNemar/bootstrap vs last `main` experiment; pin `prompt@SHA` and `model=gpt-5.4-2026-03-05` in the same commit as the image.
3. Build: `langgraph build` / `docker buildx` with `--secret`; Syft SPDX; `cosign sign` + `cosign attest --type https://spdx.dev/Document` on the **platform digest**.
4. Deploy canary RS / `setWeight: 10` (or preview Service). AnalysisJob runs 10’s **online** sample with filter `prompt.version=candidate`; coverage % is an NFR — unscored ≠ passed.
5. Promote weight 100% or flip active Service. Move Hub commit tag `prod` **after** pods that **read the tag at start** have rolled; long-lived workers must be restarted or they keep the old compiled graph.
6. Export AnalysisRun + `results.json` to WORM before default history limit **5** reaps them.

Apple Silicon local builds of `langgraph deploy` need Buildx `linux/amd64` or you ship an arm64 image into an amd64 Agent Server pool and get `exec format error` that looks like a crash loop.

### 1.4 Guardrails: rails, Presidio, Llama Guard, schema, allowlists, jailbreak

**Placement.** Guardrails are a **PEP**, not a library import in the model node. NVIDIA’s platform path: Inference Gateway **VirtualModel** middleware — `request_middleware` runs input rails **before** the backend (block ⇒ **no main-model call**); `response_middleware` runs output rails after. Production configs are **entity-backed** `config_id: "workspace/config-name"`; inline config is for tests ([Guardrails architecture](https://docs.nvidia.com/nemo-platform/documentation/guardrail-models/core-concepts/architecture)). Library path: `config.yml` rails. **IORails** engine (`NEMO_GUARDRAILS_IORAILS_ENGINE=1`) runs supported I/O/tool flows **in parallel** with admission control; custom/dialog flows fall back to **LLMRails** ([IORails](https://docs.nvidia.com/nemo/guardrails/configure-guardrails/yaml-schema/guardrails-configuration)). `parallel: true` on input/output is the older “any engine” parallel switch. **Speculative generation** (start the main LLM while input rails run) is IORails-only, **non-streaming**; streaming falls back to sequential — unsafe prompts still cost prefill, output rails must catch.

**Rail types (library schema):** input, output, retrieval, dialog, execution, action, **tool** ([configuration reference](https://docs.nvidia.com/nemo/guardrails/configure-guardrails/configuration-reference)). Built-in input flows include `llama guard check input`, `mask/detect sensitive data on input` (Presidio), `jailbreak detection heuristics`, `jailbreak detection model`, `content safety check input`, `self check input`. Output: `llama guard check output`, `mask sensitive data on output`, `injection detection`, `self check output` / facts / hallucination. 33 built-in rails under `nemoguardrails/library`; Llama Guard flows are **IORails-compiler validated** with fixed `llama_guard` model type ([rail engine support](https://docs.nvidia.com/nemo/guardrails/reference/rail-engine-support)). Extra: Presidio needs `presidio-analyzer`, `presidio-anonymizer`, spaCy `en_core_web_lg` (`sdd`).

**Presidio.** AnalyzerEngine = regex + NER + checksum + custom recognizers; Anonymizer = redact / mask / hash / replace / encrypt / fake. Microsoft’s own warning: **no guarantee of finding all PII** — additional controls required ([Presidio](https://github.com/microsoft/presidio/)). NeMo: `detect` = refuse; `mask` = continue with `*` (default) or configured token; `score_threshold` default **0.2**; raise it to cut false positives ([Presidio integration](https://docs.nvidia.com/nemo/guardrails/configure-guardrails/guardrail-catalog/third-party/presidio)). Production pattern: **sandwich** — anonymize → LLM → deanonymize via session map (PII Shield / APIM). Operators: `replace` (`{{EMAIL_1}}`), SHA-256 hash, AES-GCM encrypt, Faker ([PII Shield](https://techcommunity.microsoft.com/blog/azuredevcommunityblog/introducing-pii-shield-a-privacy-proxy-for-every-llm-call/4514726)). Do not log raw spans. Warm the spaCy model at process start or first-request p99 dies.

**Llama Guard 3 vs 4.** Classifier LLMs: generate `safe`/`unsafe` + category list. Taxonomy = MLCommons S1–S13 plus **S14 Code Interpreter Abuse** on tool-call surfaces ([LG3 8B card](https://github.com/meta-llama/PurpleLlama/blob/main/Llama-Guard3/8B/MODEL%5FCARD.md); [LG4 12B card](https://github.com/meta-llama/PurpleLlama/blob/main/Llama-Guard4/12B/MODEL%5FCARD.md)).

| SKU | Base | Modal | S14 |
| --- | --- | --- | --- |
| Llama Guard 3 1B | Llama 3.2 1B | Text; mobile pruned/quantized | No (13 cats) |
| Llama Guard 3 8B | Llama 3.1 8B | Text; 8 languages; search+code tools | Yes |
| Llama Guard 3 11B-vision | Llama 3.2 11B | **One** image + text; **not** a pure image or pure-text classifier | No |
| Llama Guard 4 12B | Llama 4 Scout | Text + **multiple images**; Moderations API | Yes, **text-only** |

NeMo: add `type: llama_guard` (vLLM OpenAI-compat, `api_key: EMPTY` if unauthenticated) and flows `llama guard check input/output` ([Llama Guard integration](https://docs.nvidia.com/nemo/guardrails/configure-guardrails/guardrail-catalog/third-party/llama-guard)). Guard 3 Vision: images rescaled to **4×560×560**; English-optimized.

**Prompt Guard 2 (jailbreak/injection, not Llama Guard).** BERT classifiers, labels **benign/malicious** only (injection label dropped vs v1); **512-token** window — split long prompts and scan **in parallel**; fine-tune on app data ([Prompt Guard 2](https://developer.meta.com/ai/docs/model-cards-and-prompt-formats/prompt-guard/)). This is the cheap first gate; Llama Guard is the taxonomy second gate.

**NeMo jailbreak heuristics.** Two published detectors, typically on a **sidecar** `server_endpoint` port **1337** `/heuristics`, perplexity via **gpt2-large**: (1) length/perplexity threshold default **89.79**; (2) prefix/suffix perplexity default **1845.65** (second-lowest among 50 GCG-style prompts → **49/50** catch, **0.04%** FPR on their non-jailbreak set); prefix/suffix only on strings with **>20** whitespace tokens ([jailbreak protection](https://docs.nvidia.com/nemo/guardrails/configure-guardrails/guardrail-catalog/jailbreak-protection)). Model-based rail: random forest on `Snowflake/snowflake-arctic-embed-m-long`. Heuristics are **LLMRails-only** for IORails (issue **#2285** backend ambiguity) — do not assume IORails parallelizes them.

**Schema and allowlists.** Tool args: JSON Schema + Pydantic last-mile (03). MCP: gateway `toolSelector` include/regex, JWT scope→tool CEL (08). Output: structured `response_format` / tool JSON, then a **code** rail (enum, `refund ≤ cap`) that does not need an LLM. Guardrails AI Hub validators (PII, toxicity, regex) are a parallel OSS stack; NeMo lists them as a third-party extra. Failure actions (fix / reask / exception / filter) must be **explicit** — silent “fix” that mutates a payment amount is a bug.

Allowlists are a **closed world**: the model may only emit tool names in a set compiled into the worker image (or hot-reloaded from a signed ConfigMap). Open-world “any MCP server the user pasted” is a laptop feature, not a SaaS PEP. Pair with **parameter** allowlists (URL prefixes, max refund, forbidden SQL verbs). Schema-invalid tool calls should **not** consume a Llama Guard GPU call — fail at JSON parse.

**Defense-in-depth order (cheap → expensive).** (1) Length/encoding/UTF-8. (2) Prompt Guard 2 / jailbreak heuristics (CPU or tiny BERT). (3) Presidio mask. (4) JSON Schema on tools. (5) Llama Guard / NVIDIA content-safety NIM. (6) Optional LLM self-check. Running (6) first is how teams spend frontier tokens classifying `"hi"`. Parallelize **independent** rails (IORails / `parallel: true`); do not parallelize mask-then-classify if the classifier must see the masked text.

**Streaming output rails.** OpenAI-class moderation on deltas is weaker than on the full message (16-production). Options: (1) rolling-window classifier (residual risk), (2) buffer then release (kills TTFT), (3) tool-call path only (JSON is finite). Document the residual.

### 1.5 Fallback chains and model routing

**Order, not hope.** Primary vendor → secondary vendor (different failure domain) → **deterministic** (template, cached answer, “try again,” queue for HITL). LiteLLM: fallbacks are **in-order model groups**; after `num_retries` (docs default **3**; router pins provider `max_retries: 0` so you do not square retries) the next group runs. `max_fallbacks` default **5**. Three maps: `fallbacks` (generic, e.g. `RateLimitError`), `context_window_fallbacks`, `content_policy_fallbacks`. Cooldown: `allowed_fails` (docs example **3** fails/min) + `cooldown_time` (example **30s**); per-deployment override under `model_info` **not** `litellm_params` (the latter is copied into the provider request). `enable_weighted_failover` retries **inside** the group first (async only). `enforce_fallback_model_access: true` skips targets the key cannot call and returns the **primary** error if none remain ([reliability](https://docs.litellm.ai/docs/proxy/reliability); [routing](https://docs.litellm.ai/docs/routing); [config](https://docs.litellm.ai/docs/proxy/config_settings)). Health-check routing **removes** deployments before users hit them; if **all** are unhealthy the filter is **bypassed** (traffic still flows — fail loud) ([health-check routing](https://docs.litellm.ai/docs/proxy/health_check_routing)). Fallback CRUD: `POST /fallback` with `STORE_MODEL_IN_DB=True` ([fallback management](https://docs.litellm.ai/docs/proxy/fallback_management)). Router settings hierarchy: **Key > Team > Global** ([key/team router](https://docs.litellm.ai/docs/proxy/keys_teams_router_settings)).

**Portkey.** `strategy.mode: fallback` + `on_status_codes: [429, 503]`; nested LB inside a target so a **cluster** outage, not one replica, triggers cross-vendor. Circuit breaker `cb_config`: `failure_threshold` or `failure_threshold_percentage` + `minimum_requests`; `cooldown_interval` **min 30s**; default failure codes **>500** — you must **list 429** if rate-limit should open the circuit. If **all** targets OPEN, breaker is **bypassed**. Retries: up to **5**, default codes include 429/5xx; exponential 1s,2s,4s,8s,16s; `use_retry_after_headers` honors provider `Retry-After` with a **60s cumulative cap** ([circuit breaker](https://portkey.ai/docs/product/ai-gateway/circuit-breaker); [fallbacks](https://portkey.ai/docs/product/ai-gateway/fallbacks); [retries](https://portkey.ai/docs/product/ai-gateway/automatic-retries)).

**Envoy** (mesh bulkhead): per-cluster max connections / pending / concurrent / **max retries**; **retry budgets** so retries cannot explode the remaining healthy hosts ([circuit breaking](https://www.envoyproxy.io/docs/envoy/latest/intro/arch_overview/upstream/circuit_breaking.html)). Apply at gateway→vendor **and** agent→MCP.

**Do not fallback on spend-cap 429.** OpenAI `organization_spend_limit_exceeded` / `project_spend_limit_exceeded` / `organization_usage_limit_exceeded` / `credit_balance_exhausted` are **not** `slow_down`. Retrying or failing over **burns the other card**. Fail over on `slow_down`, 503 `server_is_overloaded`, and regional 5xx. Deterministic last hop must **not** call a paid API.

### 1.6 Checkpoint and resume (cite 05; Temporal; saga)

**LangGraph (05).** Checkpoint = `StateSnapshot` at a **super-step**. Durability: `"exit"` (only on success/error/HITL — fastest, **no** mid-graph crash recovery), `"async"` (default — persist **while** next step runs; small loss window), `"sync"` (persist **before** next step). Agent Server default **async**. Pending writes of successful **parallel** tasks are **not** re-run; the **node body is at-least-once** (retry, `interrupt()` resume, and time-travel **restart the node from the top**). Functional API: `@task` results restore; `@entrypoint` **replays from line 1** — changing task/`interrupt` **order** mismatches cached values. `DeltaChannel` (1.2+, beta): changing live threads from delta to full snapshot **cannot reconstruct**. Graceful shutdown ≥1.2: finish current super-step, write resumable checkpoint (05 §3). PostgresSaver production: `ConnectionPool`, `autocommit=True`, `row_factory=dict_row`; `prepare_threshold=None` behind PgBouncer. Agent Server: `DATABASE_URI` (docs; **not** `POSTGRES_URI`); optional Mongo checkpointer via `LS_DEFAULT_CHECKPOINTER_BACKEND=mongo` + `LS_MONGODB_URI` — **Postgres still required** for threads/runs/assistants ([configure checkpointer](https://docs.langchain.com/langsmith/configure-checkpointer.md); [checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)). TTL on checkpoints is **forward-only**; `exit` durability reduces disk. In-process two `invoke`s on one `thread_id` **race**; Agent Server **leases** ≤1 run/thread.

**Temporal.** LangGraph plugin **public preview** (`temporalio[langgraph]`, experimental API): graph = Workflow; every node/task **must** set `execute_in: "activity"|"workflow"`; Activities get timeouts/retries/heartbeats; Workflow nodes **must be deterministic** (no I/O, `random`, clock, files). HITL `interrupt()` in an Activity node waits on a **Temporal signal — no worker CPU**. Python **3.11+** for interrupts/streaming (`contextvars` through `asyncio.create_task`). Use **`InMemorySaver`** — Temporal **is** the durability; a second Postgres checkpointer is redundant and can diverge ([Temporal LangGraph](https://docs.temporal.io/develop/python/integrations/langgraph); [plugin blog](https://temporal.io/blog/temporal-langgraph-plugin-durable-execution); [SDK plugin](https://python.temporal.io/temporalio.contrib.langgraph.LangGraphPlugin.html)). Streaming: `WorkflowStream` in `@workflow.init`; activity nodes publish via `WorkflowStreamClient` (experimental, `streaming_batch_interval`). AI reference architecture: **all** LLM and tool I/O in Activities; Workflow is the durable brain; per-tool Activity = per-tool retry/timeout ([AI reference architecture](https://go.temporal.io/platform-hub/ai-engineering/ai-reference-architecture)).

**Exactly-once tools are a lie you approximate.** Temporal Activities are **at-least-once** (worker completes, crashes before completion is recorded → retry). **At-most-once** = `maximumAttempts: 1` (zero times possible). Idempotency key = **Workflow Run ID + Activity ID** (stable across retries, unique across runs) passed to Stripe-style APIs ([error handling](https://docs.temporal.io/develop/python/best-practices/error-handling); [idempotency](https://temporal.io/blog/idempotency-and-durable-execution)). LangGraph: same rule on `ToolNode` retries (05; 03).

**Saga.** Register a compensating Activity **before** each mutating step; on failure run compensations **reversed**; log compensation failures (they fail too) then re-raise ([Python saga](https://docs.temporal.io/develop/python/best-practices/error-handling)). Compensations must themselves be idempotent. LLM “undo this email” is **not** a compensation — use provider delete APIs or a human.

**Kafka / SQS / Redis** as the async tool bus: DLQ, visibility timeout, `XACK` (16-production queue table). HTTP is the wrong API for Start-To-Close minutes.

---

## 2. Token Economics & NFR Metrics

### 2.1 Two bills: infra and tokens

API-only SaaS: **~$0 idle GPU**, token bill on every call, plus Agent Server / Temporal / Postgres. Hybrid: H100 (or similar) **whether or not** QPS is 0. ⚠️ On-demand aggregator quotes used in 16-production (2026-08): **p5.48xlarge ~$55.04/hr** us-east-1 → **~$6.88 / H100-hr**; confirm on the AWS Price List before budgeting ([DevZero](https://www.devzero.io/instances/aws/p5.48xlarge); [Thunder Compute](https://www.thundercompute.com/blog/aws-p5-vs-thunder-compute)). Cast.ai 2026 report (cited in adjacent production notes): fleet-average GPU util can be **~5%** — idle GPUs dominate hybrid cost if you copy “we might need it.”

**Platform SKUs (LangSmith, 05/10, fetched with those files 2026-09-23):** Plus **$39**/seat; LCU **$1.50**, LSU **$1.00**; runtime **0.045 LCU/vCPU-hr**, **0.006 LCU/GiB-hr**. 05 **[inferred]** Dedicated Small ~**$0.534/h ≈ $390/mo** 24×7. Tokens dominate at GPT-4.1-class ~$37/1k **ticket graphs**; platform is noise until low QPS + always-on dedicated.

**Guardrail tax.** Prompt Guard 2 / Presidio / schema = **CPU milliseconds** (⚠️ no vendor p50 published here). Llama Guard 4 **12B** is a **second model** — either a GPU replica (idle cost like any NIM) or an API classifier. NeMo `self check *` is **extra frontier tokens** on the critical path. IORails parallel hides wall-clock, not **dollar** cost. Speculative generation **bills the main model even when input rails later block**.

### 2.2 `$ per 1k production requests` with a fallback mix **[inferred]**

Prices from 01 (standard, short context, 2026-09-23): GPT-5.4 **$2.50 / $15** per 1M in/out; Claude Sonnet 4.6 **$3 / $15**; Haiku 4.5 **$1 / $5**. Skeleton **W**: one chat turn, **3,000 in + 800 out**, no cache (conservative; cache read at 0.1× drops input sharply — 01).

| Hop | $ / request **[inferred]** | $ / 1k **[inferred]** |
| --- | --- | --- |
| GPT-5.4 primary | \( (3000\times2.50 + 800\times15)/10^6 = 0.0195 \) | **$19.50** |
| Sonnet 4.6 secondary | \( (3000\times3 + 800\times15)/10^6 = 0.021 \) | **$21.00** |
| Haiku 4.5 cheap fallback | \( (3000\times1 + 800\times5)/10^6 = 0.007 \) | **$7.00** |
| Deterministic template | $0 tokens | **$0** |

**Mix M1 (API-only SaaS):** 92% primary, 6% secondary (rate-limit/5xx), 2% deterministic (schema-invalid / budget / jailbreak refuse):  
\( 0.92\times19.50 + 0.06\times21.00 + 0.02\times0 = \) **$19.20 / 1k [inferred]** tokens only.

**Mix M2 (content-policy fallback to Haiku):** 85% GPT-5.4, 10% Haiku, 5% refuse:  
\( 0.85\times19.50 + 0.10\times7.00 = \) **$17.28 / 1k [inferred]**.

**Mix M3 (retry storm anti-pattern):** 1 primary + 2 retries + 1 secondary = **4** paid calls on 5% of traffic: add \( 0.05\times(3\times19.50+21) = \$3.98 \) → **~$23.2 / 1k [inferred]** before the user saw one answer. This is why `max_fallbacks`, retry budgets, and **not retrying spend 429** are cost controls.

Agent graphs (05 scenario A: **3** LLM nodes): multiply by ~3 → **~$58.5 / 1k** GPT-5.4 tickets **[inferred]** plus HITL wait **$0** model (Agent Server / Temporal). Guardrail Llama Guard on input+output as **two 8B-class local calls** is infra, not 01 SKUs; as API-classifiers budget them as extra **~0.5–2k tokens** each **[inferred, not measured]**.

Add: embeddings, web-search tool (**$10 / 1k calls** OpenAI, 01), MCP, traces (10: LangSmith base **$0.50 / 1k** traces official — not $2.50 blog), eval canary 1–5% (10’s **$9 / 1k** Sonnet judge-only at 2k/200).

### 2.3 Idle GPU vs API; HPA/KEDA

**Idle GPU:** \( 24 \times \$6.88 \approx \$165/\mathrm{day}/\mathrm{H100} \) **[inferred]** from the aggregator SKU, **before** idle still paying for EBS/egress. Break-even vs API mix M1: \( 165 / 0.01920 \approx 8{,}600 \) requests/day **[inferred]** on that one GPU if it **replaced** GPT-5.4 tokens **and** quality matched — it does not, unless you self-host the same SKU. Real hybrid: GPU for a **pinned** open-weight SKU; API for frontier; idle GPU is a **reservation** for TTFT SLO, not a saving until utilization is measured.

**Autoscaling NFRs.** Kubernetes HPA: `desiredReplicas = ceil(current × current/desired)` , 10% tolerance, sync period typically **15s** ([HPA](https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/)). Agents waiting on APIs show **low CPU** — HPA no-ops while the queue is the SLO. llm-d + KEDA: scale on `llm_d_epp_flow_control_queue_size`, `llm_d_epp_request_running`, inflight tokens, or predicted TTFT/TPOT; WVA **deprecated** for new work; scale-to-zero buffers at EPP then cold-start becomes **request latency** ([KEDA+EPP](https://llm-d.ai/docs/dev/architecture/advanced/autoscaling/keda-epp)). `streamingMode: true` on the latency predictor trains **separate** TTFT/TPOT; mixed streaming/non-streaming **corrupts** those labels ([latency predictor](https://llm-d.ai/docs/architecture/advanced/latency-predictor)). Agent Server: KEDA on **task-queue depth**, not CPU; standalone non-Helm must DIY this ([standalone](https://docs.langchain.com/langsmith/deploy-standalone-server)).

### 2.4 OTel `gen_ai.*` as the meter (not the invoice)

Semantic conventions **Development** (not Stable). Span attrs: `gen_ai.usage.input_tokens` (**includes cache**), `output_tokens`, `cache_creation.input_tokens` / `cache_read.input_tokens` (included in input total), `reasoning.output_tokens` (included in output). When billed ≠ consumed, **report billed**. Metric `gen_ai.client.token.usage` histogram `{token}` with required `gen_ai.token.type=input|output` and `gen_ai.operation.name`; explicit buckets 1…67108864; also `gen_ai.client.operation.duration`, `time_to_first_chunk`, `time_per_output_chunk` ([metrics v1.41](https://github.com/open-telemetry/semantic-conventions/blob/v1.41.0/docs/gen-ai/gen-ai-metrics.md); [attributes](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md)). Opt-in: `OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental`.

LiteLLM emits `gen_ai.client.token.usage` **and** `gen_ai.usage.cost` (USD, computed) plus TTFT/TPOT; v2 renamed older non-standard cost/TTFT metrics — **repoint dashboards** ([OTel v1](https://docs.litellm.ai/docs/observability/opentelemetry_integration); [v2](https://docs.litellm.ai/docs/observability/opentelemetry_v2)). `litellm.call_id` joins traces to Spend Logs. `gen_ai.token.type` is **never** filterable away in v2. **Budgets are not OTel:** LiteLLM enforces against the **database**; `max_budget` **fails open** with no DB (warning at start, requests continue). `disable_spend_logs` still leaves metadata. Authoritative remaining: `litellm_remaining_team_budget_metric{team_id}` ([team budgets](https://docs.litellm.ai/docs/proxy/team_budgets); [users/budgets](https://docs.litellm.ai/docs/proxy/users)). If spend can be verified against **neither** Redis nor DB, admit path returns **503**, not a free ride (when the strict DB check is enabled).

**Per-tenant budgets (three layers).**

1. **Vendor:** OpenAI org/project **hard spend limits** (Admin API `threshold_amount` **cents**, `interval: month`); alerts vs hard cap. Hard cap → **429** `organization_spend_limit_exceeded` / `project_spend_limit_exceeded`. Enforcement **not instantaneous** — spend can slightly exceed. Separate **OpenAI-approved** monthly usage limit (tier table: Free **$100** … Tier 5 **$200k**/month after **$1,000** paid) → `organization_usage_limit_exceeded`. `Retry-After` applies to **rate** 429 / 503, **not** spend ([spend limits](https://developers.openai.com/api/docs/guides/spend-limits); [rate limits](https://developers.openai.com/api/docs/guides/rate-limits); [error codes](https://developers.openai.com/api/docs/guides/error-codes); [admin APIs](https://developers.openai.com/api/docs/guides/admin-apis)).
2. **Anthropic:** workspace spend/rate limits **≤ org**; **cannot** set limits on **Default Workspace**; unset workspace = org limits; org still applies if workspace caps **sum above** org. Claude Code workspace is the only one with **per-user** monthly spend. Usage & Cost Admin API: `/v1/organizations/usage_report/messages`, `/v1/organizations/cost_report` (USD **cents** strings, **1d** buckets). Spend-limit API (`amount` minor units, `period: monthly`, scope org/workspace) is **early-access preview** ([workspaces](https://platform.claude.com/docs/en/manage-claude/workspaces); [help center](https://support.claude.com/en/articles/9796807-creating-and-managing-workspaces-in-the-claude-console); [usage-cost API](https://platform.claude.com/docs/en/manage-claude/usage-cost-api.md); [spend_limits create](https://platform.claude.com/docs/id/api/beta/organization/spend_limits/create)).
3. **Your gateway:** LiteLLM key/team/user/tag/`model_max_budget`; Portkey budget limits. Map tenant JWT → key. Soft alert at **80%** of the **peak-minute** ceiling, not 5-minute averages (Elastic pattern, 16-production).

**429 taxonomy for the on-call runbook.** Same HTTP status, four meanings — clients and KEDA must not treat them as one signal:

| `error.code` / source | Meaning | Client | Fallback |
| --- | --- | --- | --- |
| OpenAI `slow_down` | RPM/TPM/burst | Honor `Retry-After`, jitter | Optional other vendor |
| `organization_spend_limit_exceeded` / `project_spend_limit_exceeded` | **Your** hard cap | Stop; page finance | Deterministic only |
| `organization_usage_limit_exceeded` | OpenAI-assigned tier cap | Request limit increase | Do not dump onto Anthropic without a budget |
| `credit_balance_exhausted` | Prepaid empty | Billing | No |
| LiteLLM `Budget has been exceeded` (often **400** `auth_error`, not 429) | Team/key cap | 402/403 to the user | No |
| Gateway overload 429 with `Retry-After` | **You** shed | Back off | Do not retry into the same pool |

OpenAI headers: `x-ratelimit-limit-requests`, `-tokens`, `-remaining-*`, `-reset-*`. Streaming **does not** get a cheaper pool. Anthropic evaluates **workspace and org** limiters on every request (help center). Your 402/403 for tenant budget vs 429 for overload vs 503 for no Ready endpoints is how KEDA and humans debug the right layer.

**NFR split (what to put in the SLO doc).** Availability: completed streams with `finish_reason ∈ {stop, tool_calls}` **and** not vendor spend-429 (that is **intended** shed). TTFT/TPOT: user clock (10/16). **Cost SLO:** `$ / good 1k` with fallback mix **named**. **Coverage SLO:** % of live traces with a 10-canary score. ⚠️ No vendor publishes Agent Server or NeMo p99 as of 2026-09-23.

---

## 3. Distributed Resilience & State

This is the core of the topic.

### 3.1 Durability map

| System | Unit of resume | At-least-once surface | Exactly-once approximation |
| --- | --- | --- | --- |
| LangGraph checkpointer | Super-step + pending writes | Node / tool | Idempotency keys; Agent Server lease |
| Temporal | Event history; Activity completion record | Activity body | Idempotency key; saga compensation |
| Kafka | Offset | Consumer handler | Idempotent producer + transactional outbox; DLQ |
| SQS | Receipt | Worker | `maxReceiveCount` → DLQ; FIFO ≠ exactly-once business |
| Redis Streams | PEL + `XACK` | Forgotten ACK = leak | Delivery count → DLQ stream |

Cite 05 for durability modes and node restart. Cite Temporal: disable SDK-internal LLM retries so **one** retry policy owns backoff; always set **Start-To-Close** (otherwise the server cannot detect a dead worker); heartbeat long tools; Continue-As-New before history limits ([activity failures](https://docs.temporal.io/encyclopedia/detecting-activity-failures); [timeouts](https://temporal.io/blog/activity-timeouts)). Worker Controller: rainbow / `Progressive` ramp + **gate Workflow** before Current Version (05/16).

**What belongs in which store.** Thread transcript + `next` + interrupt payload → LangGraph checkpoint (or Temporal history if the plugin owns the graph). Cross-thread facts → Store (05), not duplicated into every checkpoint. Tool **commands** (refund, ticket create) → outbox / Activity with idempotency key; never “the LLM said it so we POST again on resume.” Token bytes in flight → nowhere durable (reconnect = new request unless you checkpoint partial output yourself). Redis on Agent Server → **signaling only**; treating Redis as the checkpointer is a data-loss bug on restart.

**Kafka / SQS as the tool bus (when HTTP is wrong).** Multi-minute tools, fan-out, and “exactly-once business via outbox” belong on a log. Per-partition HOL: one slow `tools/call` stalls the partition; adding consumers does not help; `max.poll.interval.ms` (default 5 min) eviction → rebalance storm. Bound `max.poll.records`; commit **after** the Activity succeeds (at-least-once). SQS: native DLQ `maxReceiveCount`; FIFO DLQ **resets** enqueue time and **breaks** exact order; `maxReceiveCount=1` is a panic button, not resilience. Redis Streams: forgotten `XACK` = unbounded PEL; `XCLAIM` min-idle is the crash-reclaim path. Temporal remains the **saga coordinator**; Kafka is the **firehose**.

### 3.2 Circuit, bulkhead, DLQ, canary

**Circuits (three layers).** (1) Envoy cluster max outstanding. (2) LiteLLM cooldown / Portkey `cb_config`. (3) Per-tenant concurrency at the gateway (in-flight **streams**, not just RPM — TPM is lagging; OpenAI TPM counted when the request **completes**). Half-open: Portkey closes after `cooldown_interval`; LiteLLM reintroduces after `cooldown_time`. **If all open, both LiteLLM health-filter and Portkey breaker bypass** — you must shed at the edge with **429 Retry-After** instead of hanging.

**Retry policy matrix.**

| Error | Retry? | Fallback? |
| --- | --- | --- |
| 429 `slow_down` | Yes, jitter + `Retry-After` | Optional other vendor |
| 503 overloaded | Yes | Yes |
| 429 spend / usage / credit | **No** | **No** (or deterministic only) |
| 400 schema / `ContextWindowExceeded` | No | Dedicated `context_window_fallbacks` |
| Content policy | No generic | `content_policy_fallbacks` only if product says so |
| Stream mid-flight error | **No** (GKE; tokens already billed) | New request / Temporal Activity **once** with idempotent tools |

**DLQ.** Tools that fail `maxReceiveCount` times go to a queue a human or a compensator drains. Do not DLQ **chat tokens**. Do DLQ **side effects**. Kafka: app-level retry topic; HOL blocking — extra consumers **do not** help one stuck partition (16-production). SQS FIFO DLQ **resets** enqueue time and **breaks** order.

**Canary (runtime).** After 10’s CI: 1–5% live (Braintrust 1–10% high volume; LangSmith example **0.1**). Argo `AnalysisRun` on **schema_ok, PII leak rate, $ / 1k, TTFT, guardrail FP**. Shadow (preview Service) for blue/green **prePromotion**. Abort must revert **prompt tag** and **image** together — SKU pin in the new RS is useless if Hub `prod` tag already moved.

### 3.3 Checkpoint restore mismatch (class)

Resume is a **contract** on graph bytes + serde + channel set:

- Topology change (renamed node, reordered `@task` / `interrupt()`) → Functional API cache mismatch (05).
- `DeltaChannel` on / off or langgraph **<1.2** downgrade → cannot reconstruct (05).
- Serde: `JsonPlusSerializer` vs `EncryptedSerializer` / missing `LANGGRAPH_AES_KEY` → decrypt fail.
- Temporal **non-determinism** on replay (clock, LLM inside Workflow) → corrupted history.
- Agent Server Mongo vs Postgres backend switch without migration.
- Version skew: canary RS writes checkpoints the stable graph cannot read — **pin graph+checkpointer schema** in the same image.

### 3.4 Drain, PDB, hung streams

Default `terminationGracePeriodSeconds=30` vs p99 decode / super-step. Pattern: fail readiness on SIGTERM → finish or cancel → exit. 05: `request_drain()` completes the **current super-step**. NIM: live stays 200 during load; do not SIGKILL because live passed. Envoy/nginx idle timeouts **look like** hung streams. vLLM cancellation bugs (engine continues after client disconnect — 16-production issue class) ⇒ drain ≠ cancel; still **bill**.

---

## 4. Enterprise Security & Governance

### 4.1 Image SBOM and admission

SBOM on a GitHub Release is documentation. SBOM **attested on the image digest** is evidence: `cosign attest --type https://spdx.dev/Document` (or `spdxjson`) `--predicate sbom.spdx.json IMAGE@DIGEST`; verify with `cosign verify-attestation`; admit with Sigstore `ClusterImagePolicy` predicateType `https://spdx.dev/Document` or Kyverno `verifyImages` / `ImageValidatingPolicy` (v1.17+) CEL `verifyAttestationSignatures` ([Sigstore sample](https://docs.sigstore.dev/policy-controller/sample-policies/); [cosign attest](https://github.com/sigstore/cosign/blob/main/doc/cosign_attest.md); [Kyverno IVP](https://kyverno.io/docs/policy-types/image-validating-policy/); [GitHub attest-sbom](https://github.com/nirmata/demo-attestations-kyverno)). Pin **platform digest**, not the multi-arch index (NVIDIA AICR lesson, 16-production). Keyless Fulcio+Rekor + `identityRegExp` / GitHub Actions OIDC. No `:latest`. Distroless **reduces** but does not **replace** SBOM (you still attest the copied venv).

### 4.2 Secret injection

Build: secret mounts. Runtime: K8s Secret / CSI; Agent Server `REDIS_URI` / `DATABASE_URI` / `LANGSMITH_API_KEY` / `LANGGRAPH_CLOUD_LICENSE_KEY` at **process env**, not baked. License check egress `https://beacon.langchain.com` unless air-gapped ([standalone](https://docs.langchain.com/langsmith/deploy-standalone-server)). `pull_prompt(..., secrets={...}, secrets_from_env=False)` so Hub manifests cannot slurp env unless you opt in. Checkpoints: `EncryptedSerializer.from_pycryptodome_aes()` + `LANGGRAPH_AES_KEY`; identifiers may remain plaintext (05).

### 4.3 Guardrail as PEP; Zero-Trust MCP egress

North-south: gateway TLS; input rails **before** vendor; output rails **before** user. East-west: agent → MCP only through a gateway that is the PEP — OAuth 2.1, PRM RFC 9728, resource indicators RFC 8707; **no shared PAT**; tool allowlists; RFC 8707 token minted for the gateway resource **must not** work on raw GitHub MCP (08; confused deputy). NetworkPolicy: default deny; allow gateway → Agent Server → Postgres/Redis; allow NIM health port; **no** worker egress to the public internet except the MCP gateway and pinned vendor CIDRs. Sandbox untrusted tools: distroless **without** shell is insufficient if the tool is `bash`; use gVisor/Firecracker **CPU** sandboxes (08/16).

### 4.4 PII and WORM audit

Presidio on input **and** logs. Traces are PII stores (10: `hide_inputs`, datasets outlive 14d traces). Audit what a regulator will ask: `tenant`, `model` **snapshot**, `prompt_commit`, `tool name`, **hashed** args, token usage (`gen_ai.usage.*`), guardrail decision, `x-request-id`. MCP: log `tools/call` name + hash, not secrets (08). **WORM:** object-lock the audit bucket (S3 Object Lock / Azure immutable storage) for the retention your SOC2/HIPAA doc states; **OTel backends are not WORM** unless you export to that bucket. LiteLLM `redact_messages` still stores metadata/spend. ⚠️ Presidio will miss items — defense in depth, not a checkbox.

---

## 5. Production Failure Modes

| Failure | Mechanism | Blast radius | Mitigations |
| --- | --- | --- | --- |
| **GPU / process OOM** | NIM `gpu_memory_utilization` too high; agent checkpoint megabytes in RSS; distroless no shell to debug | Pod kill; in-flight SSE die; KEDA scales **up** on error latency → more OOM | Utilization 0.75–0.85 (16); cap `max_num_seqs`; checkpoint TTL / `durability=exit` for huge threads (05); **do not** liveness-loop every OOM |
| **Hung streams** | nginx **60s** read timeout; Envoy **5 min** stream idle; no SSE keepalive; vLLM ignore cancel | Partial answer; billed tokens; client reconnect doubles spend | Buffering off; idle > p99 gap; heartbeats; **do not retry** the same stream |
| **Checkpoint restore mismatch** | Graph/serde/DeltaChannel/Temporal determinism | Stuck threads; `GraphRecursionError`; Temporal non-determinism errors | Pin graph+langgraph version in the image; migrate with new `thread_id`; Temporal replay tests |
| **Canary without eval** | Argo on 5xx only; Hub `latest`; `fail-on-threshold` skipped | Silent quality + safety ship | 10’s fail-closed CI **plus** AnalysisRun on schema/PII/$/TTFT; pin SHA |
| **Budget 429** | Org/project spend or Anthropic workspace cap; LiteLLM team `max_budget`; OpenAI TPM | All tenants on that key; fallback storm | Distinguish `slow_down` vs `*_spend_limit_exceeded`; **no retry**; tenant 402/403 vs overload 429; fail-closed LiteLLM **with DB** |
| **Guardrail false positive** | Presidio PERSON; Llama Guard **S6 Specialized Advice**; heuristics on long code | Support tickets; “bot refuses everything” | Tune `score_threshold`; allowlist tools; monitor block **rate** in canary; fail-open **only** with WORM log for low-risk chat |
| **Secrets in layers** | `ARG NPM_TOKEN`; `env:` inline in `langgraph.json` | Image pull = key leak | Secret mounts; Kyverno reject images with `.env` files (defense) |
| **Spend-cap fallback** | 429 spend treated as rate-limit | Second vendor bill until **that** cap | Error-code allowlist on fallback |
| **Scale-to-zero mid-run** | HPA/KEDA min 0 on Agent Server | Lost super-step if `durability=exit`; Temporal is safer | Docs: **no serverless standalone**; minReplicas ≥ 1 interactive; Temporal for HITL days |
| **IORails / LLMRails surprise** | Custom dialog flow silently disables parallel IORails | p99 regression | `require_iorails=True` to fail init |
| **LiteLLM budget fail-open** | No DB | Unbounded $ | Require Postgres for any `max_budget` |
| **Prompt Hub RCE-config** | `include_model=True` public pull | Traffic to attacker base URL | `dangerously_pull_public_prompt` default false; pin SHA |
| **Blue/green 30s scaleDown** | Default `scaleDownDelaySeconds` | GPU not loaded; flip to NotReady | Delay ≥ NIM startup (minutes); prePromotion against preview |
| **All-circuits-open bypass** | Portkey/LiteLLM | Thundering herd on dead primary | Gateway concurrency shed first |

**Error budget.** A canary that 500s 2% of streams for 15 minutes on a 99.9% monthly SLO is not “progressive delivery” — count it (Google SRE workbook, 16-production). Freeze **prompt tags** when the budget is burned, not only “feature flags.”

---

## 6. Enterprise System Design Scenarios

### 6.1 Scenario A — API-only SaaS agent on Kubernetes

**Requirements:** multi-tenant chat+tools; no GPU; EU PII; 99.9% availability on **completed turns**; TTFT from the **vendor** plus rails.

| Layer | Choice | Reject | Why |
| --- | --- | --- | --- |
| Image | Multi-stage → distroless `python3-debian12:nonroot` digest; `langgraph build`; Cosign+SPDX | `python:3.13` as runtime; keys in `langgraph.json` | Attack surface + secret leak |
| Runtime | Helm Agent Server; `DATABASE_URI` + Redis; `minReplicas ≥ 2`; `/ok` probes; **not** serverless | Cloud Run scale-to-zero | Standalone docs: task loss |
| Gateway | LiteLLM or Portkey in-cluster; pin `gpt-5.4-2026-03-05`; tenant keys; `max_budget` **with DB** | Direct SDK from the pod | No tenant meter, no circuit |
| Guardrail | Presidio mask + Prompt Guard 2 sidecar (CPU) + schema/allowlist; Llama Guard **sampled** or async (10: judges off TTFT) | Synchronous self-check LLM on every token | Doubles $ and p99 |
| CI | promptfoo **schema/PII** `FAILURES>0`; Braintrust/LangSmith **soft Δ** per 10; pin prompt SHA | `fail-on-threshold: 100` on a judge | Flakes block forever |
| Canary | Argo 10% weight + AnalysisRun Job = 10’s online sample; coverage SLO | HTTP 200 canary | Silent regression |
| Fallback | OpenAI → Anthropic on `slow_down`/503 → deterministic refuse | Fallback on spend 429 | Double bill |
| Checkpoint | PostgresSaver pool; `durability=async`; EncryptedSerializer | Sqlite; tokens in state | 05 |
| MCP | Egress only via gateway PEP (08) | Stdio MCP in the web pod | Docs warn against stdio in servers (05) |

**NFR [inferred]:** token $ ≈ mix M1 **$19.20 / 1k** simple turns; 3-node support graph ≈ **$58 / 1k** GPT-5.4 (05 arithmetic × 01). Platform << tokens at ≥100k runs/mo. Idle GPU **$0**.

**Tenancy.** One Agent Server Deployment per **isolation domain** (prod vs sandbox), not per customer, unless a customer is a regulated island. Tenant identity from the **JWT**, not from a prompt field. LiteLLM virtual keys per tenant with `max_budget` + `budget_duration="30d"` matching the vendor month. Gateway concurrency per tenant so one noisy neighbor cannot fill `N_JOBS_PER_WORKER` (default 10) across the fleet. Postgres: RLS or a `tenant_id` column on threads; backups are **PII**. Redis DB number per deployment, not per tenant (standalone docs: share Redis **instance**, split **database number**).

**SSE and the user clock.** The Agent Server streams over Redis pub/sub; ingress must not buffer. Idle timeout > vendor TTFT + TPOT×max_tokens. Client abort still bills generated tokens if the vendor kept running — surface that on the cost dashboard or you will debug “mystery spend.”

### 6.2 Scenario B — Hybrid GPU + API with Temporal resume

**Requirements:** open-weight NIM for bulk; frontier API for hard turns; HITL that may wait **days**; tools that charge money.

| Layer | Choice | Reject | Why |
| --- | --- | --- | --- |
| Images | **Two:** distroless worker + NIM GPU image; NIMCache PVC | One image with CUDA+agent | Blast radius, size, probes |
| Orchestration | Temporal Workflow + LangGraph plugin (`execute_in=activity` on LLM/tools); `InMemorySaver` | OSS `graph.invoke` holding a FastAPI worker for HITL | 05/Temporal: signal wait is free |
| GPU scale | KEDA on EPP queue / `num_requests_waiting`; min≥1 interactive; startupProbe 20 min | CPU HPA; scale-to-zero mid-decode | llm-d + NIM Operator |
| Fallback | NIM primary → API secondary on NIM 503/queue shed → deterministic | API retry loop into NIM | Different failure domains |
| Exactly-once | Activity idempotency key = `runId+activityId`; saga compensations | “Temporal is exactly-once” | At-least-once Activities |
| Guardrail | NeMo IGW VirtualModel entity config on **both** NIM and API aliases; Presidio before **any** vendor | Rails only on API | Self-host still leaks PII to logs |
| Cost | OTel `gen_ai.client.token.usage` **100%**; LiteLLM spend DB for API tenants; GPU $ as **capacity** not per-token | Deriving GPU $ from token meters | Different bill |
| Canary | Blue/green **NIM** with prePromotion on preview (GPU already Ready) + canary **prompt** via gateway alias | Flip Service before `/v1/health/ready` | Traffic to loading weights |

**NFR [inferred]:** GPU line ≈ **$6.88/H100-hr × replicas / utilization**; API line = mix on the **overflow** fraction. A 10% API overflow at M1 rates on 100k NIM-local turns is **10k × $0.0192 ≈ $192** plus **idle GPU** if you over-provision. Temporal Cloud / self-host is a third line — keep prompts as **blob refs**, not full transcripts in history (05 size sketch).

**Resume path (scenario B, explicit).** User turn → Workflow Update → Activity `nim_complete` (Start-To-Close bound to p99 decode + load) → on 503/queue-shed Activity `api_complete` with **different** idempotency key namespace (do not reuse the NIM key on OpenAI — the providers do not share idempotency stores) → tool Activity `refund` with Stripe-style key `f"{workflow_run_id}:{activity_id}"` → `interrupt()` equivalent = Temporal signal wait (zero CPU) → compensation list executed reversed on `ApplicationError`. After a worker crash, Temporal replays Workflow decisions from history and **does not** re-call completed Activities; it **does** re-enter an Activity that never recorded completion — hence the key. LangGraph `durability=async` on a sidecar checkpointer **in addition** to Temporal is how teams get two sources of truth and a mismatch on the next deploy (05: plugin says InMemorySaver).

**Capacity sketch [inferred, not a vendor SLO].** One NIM replica Ready after ≤20 min startup probe budget; KEDA scale-up must be faster than that or you add NotReady pods while the queue is already the SLO miss. Keep `minReplicaCount ≥ 1` for interactive SKUs; use `activationThreshold` so a single probe message does not wake a second GPU. API overflow pool is **stateless** and can scale on concurrency / Redis queue depth independently of HBM.

### 6.3 Trade-off matrices

**Distroless vs slim vs NVIDIA CUDA**

| | Distroless python/cc | `python:slim` | NIM/CUDA |
| --- | --- | --- | --- |
| Shell / CVE surface | None / tiny | apt | Large |
| GPU kernels | No | No | Yes |
| Debug | `:debug` busybox; ephemeral | kubectl exec | Same |
| Agent worker | **Default** | If native `.so` hell | Wrong |
| Healthcheck binary | No `curl` — use distroless-compatible probe or a tiny static probe | curl OK | NIM has `/v1/health/*` |

**Canary vs blue/green vs Hub tag-only**

| | Canary weight | Blue/green | Move `prod` tag |
| --- | --- | --- | --- |
| Blast | 1–10% | 100% after flip | 100% immediately |
| GPU cost | +slice | **2×** until scaleDown | 0 extra |
| Rollback | Weight 0 | Selector flip back | Move tag **if** workers pull on start only — long-lived workers **won’t** |
| Needs 10’s gate | Yes | Yes (prePromotion) | Yes, or it is a footgun |

**Checkpointer vs Temporal vs Kafka**

| Need | Pick |
| --- | --- |
| Chat thread time-travel, HITL minutes, Agent Server | LangGraph Postgres (05) |
| HITL **days**, worker crash, multi-step money | Temporal + saga |
| Fan-out 100k tools/s | Kafka + DLQ; Temporal for the **saga** not the firehose |

### 6.4 Interview close

A Principal who draws “K8s + LangGraph” is incomplete. The production diagram is: **signed distroless (or NIM) digest → secret injection not layers → Helm Agent Server / Temporal workers with drain → gateway that pins SKUs, meters `gen_ai.*`, enforces tenant budgets, and fails over on the right 429 → guardrail PEP (Presidio + Prompt Guard + Llama Guard + schema) off or on the SLO by design → 10’s fail-closed CI plus Argo evidence-gated canary → checkpoints that resume the same graph bytes.** The model is a **stateless token API**. Everything else is this ops plane.

---

## Sources

1. https://docs.langchain.com/langsmith/deploy-standalone-server — Standalone Agent Server Docker/K8s; `DATABASE_URI`/`REDIS_URI`; `/ok`; no serverless
2. https://docs.langchain.com/langsmith/cli — `langgraph build`/`deploy`/`dockerfile`; pricing flags; `/ok` survives `disable_meta`
3. https://docs.langchain.com/langsmith/deployment-quickstart — `langgraph deploy` beta; remote build
4. https://docs.langchain.com/langsmith/deployment — Control vs data plane; Cloud/Hybrid/self-host
5. https://docs.langchain.com/langsmith/agent-server — Assistants/threads/runs; Redis ephemeral; 1 run/thread
6. https://docs.langchain.com/langsmith/custom-docker — `dockerfile_lines`
7. https://docs.langchain.com/langsmith/configure-checkpointer.md — Postgres default; Mongo `LS_DEFAULT_CHECKPOINTER_BACKEND`; Postgres still required
8. https://docs.langchain.com/oss/python/langgraph/checkpointers — Durability `exit`/`async`/`sync`; PostgresSaver
9. https://docs.langchain.com/oss/python/langgraph/fault-tolerance — At-least-once nodes; drain; RetryPolicy
10. https://docs.langchain.com/langsmith/manage-prompts-programmatically — Prompt+model push/pull; commit SHA
11. https://docs.langchain.com/langsmith/manage-prompts — Commit tags vs resource tags
12. https://reference.langchain.com/python/langsmith/client/Client/pull_prompt — `include_model`; `dangerously_pull_public_prompt`; secrets map
13. https://langchain-ai-langgraph-40.mintlify.app/cli/configuration — `langgraph.json` `env`; do not commit keys
14. https://kb.langchain.com/articles/6253531756-understanding-checkpointers-databases-api-memory-and-ttl — ConnectionPool; TTL; `exit` vs disk
15. https://github.com/GoogleContainerTools/distroless — Distroless images
16. https://github.com/GoogleContainerTools/distroless/pull/1415 — python3-debian12 no shell/ctypes
17. https://github.com/GoogleContainerTools/distroless/issues/1409 — python3 vs python3-plus
18. https://github.com/kagent-dev/kagent/blob/74321ee6/python/Dockerfile — Distroless cc-debian12 digest pin; UID 65532
19. https://github.com/astral-sh/uv/blob/2318e48e/docs/guides/integration/docker.md — uv multi-stage `--no-editable`
20. https://docs.docker.com/build/building/secrets/ — BuildKit secret mounts
21. https://oneuptime.com/blog/post/2026-02-08-how-to-use-run-mounttypesecret-for-build-time-secrets/view — ARG/ENV vs secret mount
22. https://catalog.ngc.nvidia.com/orgs/nvidia/distroless/containers/python/3.12-v4.0.3 — NVIDIA Python distroless
23. https://docs.nvidia.com/nim-operator/latest/service.html — live/ready/startup probes; 20 min default
24. https://docs.nvidia.com/nim/large-language-models/2.0.3/reference/architecture.html — `/v1/health/live` vs `ready`; NIM_HEALTH_PORT
25. https://docs.nvidia.com/nim/large-language-models/latest/get-started/quickstart.html — Health curl; SSE `[DONE]`
26. https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/ — HPA formula
27. https://kubernetes.github.io/ingress-nginx/user-guide/nginx-configuration/configmap/ — `proxy-read-timeout` 60s
28. https://kubernai.com/articles/streaming-ingress-for-llm-responses — SSE buffering off; 504 on 60s
29. https://www.envoyproxy.io/docs/envoy/latest/faq/configuration/timeouts.html — `stream_idle_timeout` 5 min; route timeout 0 for streams
30. https://www.envoyproxy.io/docs/envoy/latest/intro/arch_overview/upstream/circuit_breaking.html — Envoy circuit breakers / retry budgets
31. https://llm-d.ai/docs/dev/architecture/advanced/autoscaling — Do not scale on GPU util
32. https://llm-d.ai/docs/dev/architecture/advanced/autoscaling/keda-epp — KEDA+EPP queue metrics; scale-from-zero
33. https://llm-d.ai/docs/architecture/advanced/latency-predictor — `streamingMode` TTFT vs e2e
34. https://argoproj.github.io/argo-rollouts/features/bluegreen/ — active/preview; pre/post analysis
35. https://argoproj.github.io/argo-rollouts/features/analysis/ — AnalysisRun; Job provider; history limit 5
36. https://github.com/promptfoo/promptfoo-action/ — `fail-on-threshold`; `repeat`/`repeat-min-pass`; Node 24
37. https://www.promptfoo.dev/docs/integrations/ci-cd/ — `jq` failures gate; artifacts
38. https://www.promptfoo.dev/docs/integrations/github-action/ — PR before/after; cache
39. https://www.braintrust.dev/docs/evaluate/run-in-ci — `eval-action@v2`; Reporter; `--sample`
40. https://docs.nvidia.com/nemo-platform/documentation/guardrail-models/core-concepts/architecture — IGW VirtualModel PEP; entity vs inline config
41. https://docs.nvidia.com/nemo/guardrails/configure-guardrails/yaml-schema/guardrails-configuration — IORails; parallel; speculative generation
42. https://docs.nvidia.com/nemo/guardrails/configure-guardrails/configuration-reference — Rail types; Presidio threshold 0.2; jailbreak flows
43. https://docs.nvidia.com/nemo/guardrails/reference/rail-engine-support — 33 rails; Llama Guard IORails; heuristics #2285
44. https://docs.nvidia.com/nemo/guardrails/configure-guardrails/guardrail-catalog/third-party/presidio — detect vs mask; spaCy
45. https://docs.nvidia.com/nemo/guardrails/configure-guardrails/guardrail-catalog/third-party/llama-guard — vLLM OpenAI-compat wiring
46. https://docs.nvidia.com/nemo/guardrails/configure-guardrails/guardrail-catalog/jailbreak-protection — 89.79 / 1845.65; 49/50 GCG; 0.04% FPR
47. https://github.com/microsoft/presidio/ — PII; no completeness guarantee
48. https://techcommunity.microsoft.com/blog/azuredevcommunityblog/introducing-pii-shield-a-privacy-proxy-for-every-llm-call/4514726 — Sandwich anonymize/deanonymize
49. https://github.com/meta-llama/PurpleLlama/blob/main/Llama-Guard3/8B/MODEL%5FCARD.md — S1–S14; 8B
50. https://github.com/meta-llama/PurpleLlama/blob/main/Llama-Guard3/1B/MODEL_CARD.md — 1B; 13 categories
51. https://github.com/meta-llama/PurpleLlama/blob/main/Llama-Guard3/11B-vision/MODEL_CARD.md — One image; 4×560
52. https://github.com/meta-llama/PurpleLlama/blob/main/Llama-Guard4/12B/MODEL%5FCARD.md — 12B multimodal; S14 text-only
53. https://developer.meta.com/ai/docs/model-cards-and-prompt-formats/prompt-guard/ — Prompt Guard 2 BERT; 512 tokens; benign/malicious
54. https://github.com/open-telemetry/semantic-conventions/blob/v1.41.0/docs/gen-ai/gen-ai-metrics.md — `gen_ai.client.token.usage`; billed tokens
55. https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md — cache/reasoning token attrs
56. https://github.com/open-telemetry/semantic-conventions/blob/v1.41.0/docs/gen-ai/gen-ai-spans.md — Stability opt-in
57. https://docs.litellm.ai/docs/observability/opentelemetry_integration — LiteLLM OTel metrics + `gen_ai.usage.cost`
58. https://docs.litellm.ai/docs/observability/opentelemetry_v2 — Metric rename; `gen_ai.token.type` unfilterable
59. https://docs.litellm.ai/docs/proxy/users — Budgets need DB; fail-open warning
60. https://docs.litellm.ai/docs/proxy/team_budgets — `budget_duration`; Prometheus remaining metric
61. https://docs.litellm.ai/docs/proxy/reliability — Ordered fallbacks; `enforce_fallback_model_access`
62. https://docs.litellm.ai/docs/routing — `max_fallbacks` 5; cooldown; `num_retries` vs `max_retries`
63. https://docs.litellm.ai/docs/proxy/config_settings — `allowed_fails` / `cooldown_time` examples
64. https://docs.litellm.ai/docs/proxy/health_check_routing — Proactive pool; all-unhealthy bypass
65. https://docs.litellm.ai/docs/proxy/fallback_management — `/fallback` CRUD; types
66. https://docs.litellm.ai/docs/proxy/keys_teams_router_settings — Key > Team > Global
67. https://portkey.ai/docs/product/ai-gateway/circuit-breaker — CB fields; all-OPEN bypass; min 30s
68. https://portkey.ai/docs/product/ai-gateway/fallbacks — `on_status_codes`; nested strategies
69. https://portkey.ai/docs/product/ai-gateway/automatic-retries — 5 attempts; 60s cap; Retry-After
70. https://developers.openai.com/api/docs/guides/spend-limits — Hard cap 429; not instantaneous
71. https://developers.openai.com/api/docs/guides/rate-limits — Tiers $100–$200k; `slow_down`; TPM at complete
72. https://developers.openai.com/api/docs/guides/error-codes — Spend vs usage vs rate 429 codes
73. https://developers.openai.com/api/docs/guides/admin-apis — Spend limit cents POST
74. https://help.openai.com/en/articles/5955604 — Do not retry billing 429
75. https://platform.claude.com/docs/en/manage-claude/workspaces — Workspace ≤ org; no Default Workspace limits
76. https://support.claude.com/en/articles/9796807-creating-and-managing-workspaces-in-the-claude-console — Console Limits tab
77. https://platform.claude.com/docs/en/manage-claude/usage-cost-api.md — Usage/cost admin APIs; 1d buckets
78. https://platform.claude.com/docs/id/api/beta/organization/spend_limits/create — Early-access spend_limits
79. https://docs.temporal.io/develop/python/integrations/langgraph — Plugin; `execute_in`; InMemorySaver; Py 3.11+
80. https://temporal.io/blog/temporal-langgraph-plugin-durable-execution — Public preview; HITL signal wait
81. https://python.temporal.io/temporalio.contrib.langgraph.LangGraphPlugin.html — Experimental; streaming_topic
82. https://go.temporal.io/platform-hub/ai-engineering/ai-reference-architecture — LLM/tools as Activities
83. https://docs.temporal.io/develop/python/best-practices/error-handling — Idempotency; saga reverse
84. https://temporal.io/blog/idempotency-and-durable-execution — At-least-once vs at-most-once
85. https://docs.temporal.io/encyclopedia/detecting-activity-failures — Start-To-Close
86. https://temporal.io/blog/activity-timeouts — Timeout kinds
87. https://docs.sigstore.dev/policy-controller/sample-policies/ — SPDX attestation ClusterImagePolicy
88. https://github.com/sigstore/cosign/blob/main/doc/cosign_attest.md — `cosign attest --type`
89. https://kyverno.io/docs/policy-types/image-validating-policy/ — ImageValidatingPolicy CEL
90. https://sre.google/workbook/implementing-slos/ — Error budget
91. https://www.langchain.com/pricing — LCU/LSU (with 05/10)
92. https://docs.langchain.com/langsmith/usage-and-billing — Trace units (10)
93. https://www.devzero.io/instances/aws/p5.48xlarge — ⚠️ Aggregator p5 price
94. https://www.thundercompute.com/blog/aws-p5-vs-thunder-compute — ⚠️ Aggregator H100-hr
95. https://modelcontextprotocol.io/specification/2026-07-28 — MCP spec (08)
96. https://docs.langchain.com/oss/python/langgraph/pregel — Super-step checkpoints (05)
97. https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-dead-letter-queues.html — SQS DLQ; FIFO order break
98. https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/ — Probes; Docker HEALTHCHECK unused
99. https://github.com/nirmata/demo-attestations-kyverno — GitHub attest-sbom + Kyverno
100. https://github.com/temporalio/sdk-python/pull/1500 — WorkflowStream LangGraph streaming experimental

**Coverage confirmation:** Docker (multi-stage, GPU vs CPU, secret mounts, distroless, healthchecks/probes, streaming ingress); CI/CD (10’s eval gates, promptfoo/Braintrust, Argo canary/blue-green, Hub SHA + SKU pin); guardrails (NeMo IORails/IGW, Presidio, Llama Guard 3/4, Prompt Guard 2, schema/allowlists, jailbreak heuristics); cost (01 SKUs, **[inferred]** $/1k mixes, idle GPU vs API, OTel `gen_ai.*`, LiteLLM/OpenAI/Anthropic caps, spend vs rate 429); fallbacks (LiteLLM/Portkey/Envoy; deterministic last hop); checkpoint/resume (05 durability, Temporal plugin, saga, idempotency); six dimensions (topology four planes, token economics, resilience core, SBOM/secrets/PEP/WORM, failure table, two scenarios).

---

## Gaps

> ⚠️ **Limited public data** for: (1) production p50/p95/p99 of NeMo IORails, Presidio spaCy, Llama Guard 4 12B, or Agent Server `/ok` under load — none published as contractual SLOs as of 2026-09-23; (2) NVIDIA’s “not production-as-is” disclaimer on older Guardrails library versions vs current IGW entity-backed path — quote the doc **you deploy**; (3) Prompt Guard 2 86M “20–50ms on H100 FP8” figures circulating in blogs were **not** re-verified on Meta’s 2026-09-23 card (card specifies 512-token BERT, not that latency); (4) AWS GPU $/hr above are **third-party aggregators**, not Price List API; (5) Temporal LangGraph plugin is **public preview / experimental** — treat as non-GA for regulated go-live; (6) LiteLLM `allowed_fails: 3` / `cooldown_time: 30` are **doc examples**, not universal defaults you should copy without measuring; (7) Anthropic Console spend-limit **API** is early-access; (8) OpenAI hard-limit enforcement delay is acknowledged but **not quantified**; (9) no public multi-tenant SaaS paper with a measured fallback mix matching M1–M3 — those `$ / 1k` rows are **[inferred]** arithmetic; (10) WORM-on-OTel is a **pattern**, not a vendor guarantee.
