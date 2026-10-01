# Module 06 — How RAG Works

**Audience**: Principal AI Architect interview prep · personal deep study  
**Sequence**: 06 (retrieval + grounding systems)  
**Grounded in**: `research/06-how-rag-works.md` (28 sources, 2026-09-30)

Retrieval-Augmented Generation (RAG) couples a **parametric** language model with a **non-parametric** external knowledge index. Lewis et al. (NeurIPS 2020) formalize generation as conditioning a seq2seq model on passages retrieved from a dense index (DPR bi-encoder + FAISS MIPS/HNSW), treating documents as latent variables marginalized at decode time ([Lewis et al., 2020 — arXiv](https://arxiv.org/abs/2005.11401)). Production systems split **offline ingest/index** from **online retrieve→fuse→rerank→generate**, with hybrid sparse+dense retrieval as the default quality floor ([Anthropic Contextual Retrieval](https://www.anthropic.com/engineering/contextual-retrieval); [System Design Newsletter — How RAG Works](https://newsletter.systemdesign.one/p/how-rag-works)).

---

## Part 1 — System Topology & Data Flow

### Architecture map

```
                    ┌─────────────────────────────────────────────────────────────────┐
                    │                        CONTROL PLANE                            │
                    │  ingest triggers · index versioning / blue-green swap           │
                    │  ACL + metadata policy · strategy (sparse|dense|hybrid)         │
                    │  top-K / rerank budgets · query router / CRAG evaluator          │
                    │  deadline budget · circuit breakers · max agentic hops          │
                    └───────────────┬───────────────────────────────┬─────────────────┘
                                    │ policy + budgets              │
                                    ▼                               ▼
┌──────────────────────────────────────────┐   ┌──────────────────────────────────────┐
│              DATA PLANE                  │   │         TOOL PROXIES                 │
│  INGEST: load→chunk→(contextualize)→     │   │  MCP retrieve.* tools                │
│          embed→BM25 build→index write    │   │  embed / BM25 / ANN / rerank proxies │
│  QUERY:  embed_q ‖ BM25 → RRF fuse →     │   │  LLM generate proxy                  │
│          rerank → prompt → generate      │   │  web-search fallback (CRAG)          │
└───────┬──────────────────────┬───────────┘   └──────────────────┬───────────────────┘
        │                      │                                  │
        ▼                      ▼                                  ▼
┌──────────────────┐  ┌────────────────────┐           ┌──────────────────────────────┐
│   PERSISTENCE    │  │    TELEMETRY       │◄──────────┤  correlation IDs · stage spans│
├──────────────────┤  ├────────────────────┤           │  nDCG / recall@k / fail@20   │
│ object store/CMS │  │ query + audit logs │           │  p50/p95/p99 · $/1k queries  │
│ chunk+ACL meta   │  │ index build metrics│           │  breaker state · DLQ counts  │
│ vector ANN index │  │ cost / token meters│           └──────────────────────────────┘
│ BM25 inverted ix │  └────────────────────┘
│ index alias vN   │
└──────────────────┘
```

**Plane responsibilities**

| Plane | What lives here | Truth source |
| --- | --- | --- |
| **CONTROL PLANE** | Ingestion schedules, index version/alias, ACL filter compilation, retrieval strategy + K/rerank budgets, agentic router/CRAG actions, deadlines and breakers | Offline schedulers; blue-green corpora; IAM→filter; CRAG/Self-RAG control tokens ([Newsletter](https://newsletter.systemdesign.one/p/how-rag-works); [CRAG](https://arxiv.org/html/2401.15884v3); [AWS Bedrock metadata filtering](https://aws.amazon.com/blogs/machine-learning/access-control-for-vector-stores-using-metadata-filtering-with-knowledge-bases-for-amazon-bedrock/)) |
| **DATA PLANE** | Chunk→embed→search→fuse→rerank→prompt→generate hot path; parallel ingest workers | Embedding workers; inverted + vector indexes; RRF; cross-encoder/Cohere rerank; LLM prefill/decode ([Lewis et al., 2020](https://arxiv.org/abs/2005.11401); [Elastic hybrid](https://www.elastic.co/search-labs/blog/improving-information-retrieval-elastic-stack-hybrid)) |
| **PERSISTENCE** | Source corpus, chunk↔ACL metadata, BM25 + ANN indexes, atomic index alias | Object store/CMS + dual indexes; never mix embedding-model versions in one space ([Lewis et al., 2020](https://arxiv.org/abs/2005.11401); research §3) |
| **TOOL PROXIES** | MCP-gated retrieve/embed/rerank/generate; optional web search | Least-privilege tool hosts; retrieved text treated as untrusted data ([VaultRAG](https://github.com/AgentPostmortem/VaultRAG); research §4) |
| **TELEMETRY** | Stage latency, retrieval quality, audit of chunk IDs returned/denied, cost meters | Append-only query/audit logs; nDCG / recall@k / fail@20 ([Elastic](https://www.elastic.co/search-labs/blog/improving-information-retrieval-elastic-stack-hybrid); [Anthropic](https://www.anthropic.com/engineering/contextual-retrieval)) |

### Request-flow narrative (ingest vs query)

**A. Offline ingest / index (control-plane scheduled)**

1. **Trigger** — CMS webhook, cron, or Kafka topic fires an ingest workflow for tenant/corpus `C`.
2. **Load + ACL attach** — Pull documents from object store; attach `tenant_id`, `allowed_groups`, classification on every chunk (deny-by-default if ACL missing) ([AWS Bedrock filtering](https://aws.amazon.com/blogs/machine-learning/access-control-for-vector-stores-using-metadata-filtering-with-knowledge-bases-for-amazon-bedrock/); [VaultRAG](https://github.com/AgentPostmortem/VaultRAG)).
3. **Chunk** — Fixed ~300–512 tokens with 10–20% overlap as the practical default; Lewis used ~100-word Wikipedia passages; Anthropic: “a few hundred tokens” ([Lewis et al., 2020](https://arxiv.org/abs/2005.11401); [Qu et al., 2025](https://aclanthology.org/2025.findings-naacl.114.pdf); [Anthropic](https://www.anthropic.com/engineering/contextual-retrieval)).
4. **Optional contextualize** — Prepend 50–100 tokens of chunk-specific context (Haiku-class) before embed **and** BM25 ([Anthropic Contextual Retrieval](https://www.anthropic.com/engineering/contextual-retrieval)).
5. **Embed + sparse build** — Write vectors to ANN (HNSW/IVF) and terms to inverted index; stamp `chunk_strategy`, `embedding_model`, `index_version`.
6. **Atomic alias swap** — Build `vN+1` offline → flip alias when consistent; generator weights stay fixed while non-parametric memory updates ([Lewis et al., 2020](https://arxiv.org/abs/2005.11401)).
7. **Telemetry** — Ingest tokens, contextualize $, embed QPS, build duration → dashboards.

**B. Online query (sync request/response; agentic = multi-hop loop)**

1. **Auth → filter compile** — Resolve principal groups from IAM (not client-asserted roles); compile mandatory prefilters for ANN/BM25 ([VaultRAG](https://github.com/AgentPostmortem/VaultRAG); [Truto](https://truto.one/blog/how-to-maintain-document-level-rbac-in-enterprise-rag-pipelines)).
2. **Embed query** with the **same** embedding model as the index; run BM25 in parallel when hybrid.
3. **Fuse** — Reciprocal Rank Fusion: \(\mathrm{score}(d)=\sum_i 1/(k+\mathrm{rank}_i(d))\) ([Elastic hybrid](https://www.elastic.co/search-labs/blog/improving-information-retrieval-elastic-stack-hybrid)).
4. **Optional rerank** — Cross-encoder / Cohere over fused candidates (e.g. top-150 → top-20) ([Anthropic](https://www.anthropic.com/engineering/contextual-retrieval)).
5. **Prompt assemble** — System + delimited retrieved chunks (edges for high-score docs—lost-in-the-middle) + user query ([Liu et al., 2024](https://arxiv.org/abs/2307.03172)).
6. **Generate + cite** — LLM decode; audit `{principal, query, filter, chunk_ids, index_version, model}`.
7. **Agentic branch** — Self-RAG reflection tokens / CRAG Correct|Ambiguous|Incorrect may re-retrieve, web-search, or abstain; cap hops (`max_depth` default 6 in Self-RAG ref) ([Self-RAG](https://proceedings.iclr.cc/paper_files/paper/2024/file/25f7be9694d7b32d5cc670927b8091e1-Paper-Conference.pdf); [CRAG](https://arxiv.org/html/2401.15884v3)).

---

## Part 2 — Core Mechanics & Algorithms

### Theoretical fundamentals

RAG approximates \(p(y \mid x) \approx \sum_{z \in \mathrm{top}\text{-}k} p_\eta(z \mid x)\, p_\theta(y \mid x, z)\). Two marginalization topologies ([Lewis et al., 2020](https://arxiv.org/abs/2005.11401)):

| Topology | Behavior | Test-time \(k\) (paper) |
| --- | --- | --- |
| **RAG-Sequence** | One retrieved doc conditions the whole answer | up to **50** |
| **RAG-Token** | Different docs can contribute per token | up to **15** |

Non-parametric memory in the paper: Dec 2018 Wikipedia → **21,015,324** ~100-word chunks; train \(k \in \{5,10\}\) ([Lewis et al., 2020](https://arxiv.org/abs/2005.11401)).

### Retrieval strategies

| Strategy | Algorithm | Strength | Weakness |
| --- | --- | --- | --- |
| **Sparse (BM25)** | TF–IDF + length norm + TF saturation | Exact IDs / error codes / rare terms | Paraphrase miss |
| **Dense** | Bi-encoder + ANN (HNSW/IVF MIPS) | Semantic paraphrase | Identifier miss; embedding drift |
| **Hybrid** | Both → RRF / weighted sum → optional rerank | Stacked gains on BEIR-style and Anthropic evals | Latency if sequential; dual-index ops |

**RRF complexity**: merge \(R\) ranked lists of length \(L\): \(O(R L \log (R L))\) with heap/map; typically \(R=2\), \(L \le 100\). Elastic: RRF of BM25 + learned sparse lifted avg **nDCG@10 by 1.4%** vs sparse alone and **18%** vs BM25 alone; calibrated linear fusion up to **+6% / +24%** but needs ~40–300 labeled queries ([Elastic hybrid](https://www.elastic.co/search-labs/blog/improving-information-retrieval-elastic-stack-hybrid)).

### Contextual retrieval (index-time enrichment)

Anthropic prepends chunk-specific context before embed **and** BM25. On their multi-domain eval (Gemini Text 004, top-20) ([Anthropic Contextual Retrieval](https://www.anthropic.com/engineering/contextual-retrieval)):

| Pipeline | Failure @20 (\(1-\mathrm{recall}@20\)) | Relative reduction |
| --- | --- | --- |
| Baseline dense | 5.7% | — |
| Contextual Embeddings | 3.7% | **35%** |
| + Contextual BM25 | 2.9% | **49%** |
| + Cohere rerank (150→20) | 1.9% | **67%** |

### Chunking & ANN internals

- **Fixed/recursive** (~300–512 tok, 10–20% overlap) remains default; NAACL 2025 Findings: semantic chunking’s compute is **not consistently justified** on non-synthetic corpora ([Qu et al., 2025](https://aclanthology.org/2025.findings-naacl.114.pdf)).
- **Parent-child / RAPTOR**: retrieve children, expand parents; tree for multi-hop ([Sarthi et al., 2024](https://arxiv.org/abs/2401.18059)).
- **Late chunking**: long-context embed full doc → pool per-chunk vectors ([Günther et al., 2024](https://arxiv.org/abs/2409.04701)).
- **HNSW**: \(M\) neighbors/vector; `efSearch` trades recall vs latency. FAISS SIFT1M (20 threads): `efSearch=16` → **0.011 ms/query**, R@1 **0.874**; `efSearch=64` → **0.033 ms**, R@1 **0.978**; memory ≈ \(4d + 8M\) bytes/vector ([FAISS 1M](https://github.com/facebookresearch/faiss/wiki/Indexing-1M-vectors)).

**Invariant**: one ANN space = one `(embedding_model, dim, distance)` triple. Changing any forces full reindex.

### Agentic control loops

| Pattern | Control | Published signal |
| --- | --- | --- |
| **Self-RAG** | Reflection tokens `Retrieve` / `ISREL` / `ISSUP` / `ISUSE` | Self-RAG 7B PopQA **54.9**, citation precision ASQA **70.3** vs Ret-ChatGPT **39.9** ([Self-RAG](https://proceedings.iclr.cc/paper_files/paper/2024/file/25f7be9694d7b32d5cc670927b8091e1-Paper-Conference.pdf)) |
| **CRAG** | Evaluator → Correct / Ambiguous / Incorrect; web fallback | PopQA **59.8** vs RAG **52.8** on SelfRAG-LLaMA2-7b ([CRAG](https://arxiv.org/html/2401.15884v3)) |
| **Router agents** | No-retrieve vs single-shot vs multi-step / which index | Engineering pattern ([LlamaIndex](https://www.llamaindex.ai/blog/rag-is-dead-long-live-agentic-retrieval)) |

### Lost-in-the-middle (generation invariant)

Liu et al.: U-shaped accuracy vs passage position; middle placement in 20–30 doc settings can fall **below closed-book** (GPT-3.5-Turbo closed-book **56.1%** vs oracle **88.3%**). Going **20→50** docs gains only ~**1–1.5%** while blowing tokens/latency—prefer rerank-to-front and smaller K ([Liu et al., 2024](https://arxiv.org/abs/2307.03172)).

---

## Part 3 — Token Economics & NFR Analysis

### Cost formula: `$ per 1k runs` (queries)

Define **1 run = 1 online RAG query** (retrieve + generate). Separate **one-time index** cost.

#### A. Index-time contextualization (published)

Anthropic: **$1.02 per million document tokens** with prompt caching under assumptions: 800-token chunks, 8K-token documents, 50-token instructions, ~100-token generated context per chunk ([Anthropic Contextual Retrieval](https://www.anthropic.com/engineering/contextual-retrieval)).

\[
\text{Cost}_{\text{contextualize}} = \frac{T_{\text{doc}}}{10^6} \times \$1.02
\]

Example: 50M doc tokens → \(50 \times \$1.02 = \$51\) one-time (plus embed; see below).

#### B. Online query cost per 1k runs

**Assumptions (label explicitly; verify live SKUs before budgeting)**

| Parameter | Value | Label |
| --- | --- | --- |
| Generator input | **$3.00 / MTok** | **[assumed]** Claude-class mid-tier illustrative rate from research (§2) |
| Generator output | **$15.00 / MTok** | **[assumed]** same class; not a live SKU quote |
| Chunk size | ~500 tokens | research worked example |
| System + query overhead | 1,000 tokens | **[assumed]** |
| Output tokens / answer | 400 | **[assumed]** |
| Embed query | **$0.02 / MTok** | OpenAI `text-embedding-3-small` aggregator listing (re-check) ([ModelPriceWatch](https://modelpricewatch.com/models/openai-text-embedding-3-small/)) |
| Rerank | **$2.00–$2.50 / 1K searches** | Cohere pay-as-you-go cards; **verify live** ([Cohere pricing](https://cohere.com/pricing)) — research ⚠️ limited |
| Prompt-cache hit on static system prefix | 90% cost cut on cached tokens when warm | Anthropic “up to 90%” ([Anthropic](https://www.anthropic.com/engineering/contextual-retrieval)) |

**Context tokens by strategy** (research worked table, ~500 tok/chunk):

| Strategy | Context tokens / query | Generator input $ / 1k queries @ $3/MTok |
| --- | --- | --- |
| Stuff 200K-token KB (no cache) | 200,000 | \(200 \times \$3 = \$600\) |
| Stuff + ~90% cache on static prefix | ~20k full + 180k @ ~0.1× | **[inferred]** ~\$78 / 1k |
| RAG top-5 | ~2,500 + overhead | \(2.5 \times \$3 = \$7.50\) (chunks only) |
| RAG top-20 (Anthropic preferred K) | ~10,000 + overhead | \(10 \times \$3 = \$30\) (chunks only) |

**Full per-1k-runs formula (RAG top-20 + rerank + generate)**:

\[
\begin{aligned}
T_{\text{in}} &= T_{\text{sys+q}} + 20 \times 500 = 1{,}000 + 10{,}000 = 11{,}000 \\
T_{\text{out}} &= 400 \\
C_{\text{gen}} &= 1000 \times \Bigl(\tfrac{T_{\text{in}}}{10^6}\cdot 3 + \tfrac{T_{\text{out}}}{10^6}\cdot 15\Bigr)
  = 1000 \times (0.033 + 0.006) = \$39 \\
C_{\text{embed}_q} &= 1000 \times \tfrac{T_{\text{q}}}{10^6}\cdot 0.02 \quad (T_{\text{q}}\approx 50 \Rightarrow \approx \$0.001) \\
C_{\text{rerank}} &\approx 1000 \times \$0.00225 = \$2.25 \quad \text{([assumed] mid of \$2–\$2.50/1K searches)} \\
C_{\text{1k runs}} &\approx C_{\text{gen}} + C_{\text{rerank}} + C_{\text{embed}_q} \approx \$41.25
\end{aligned}
\]

With warm **prompt cache** on static system (~1k tokens) at 10% input price on those tokens, subtract roughly \(1000 \times 0.9 \times (1000/10^6)\times 3 \approx \$2.7\) → **~\$38.5 / 1k runs** **[inferred]**. Stuffing a cold 200K KB remains ~**\$600 / 1k** input-only—order-of-magnitude worse than RAG top-20 when cache is cold ([Anthropic](https://www.anthropic.com/engineering/contextual-retrieval)).

**Takeaway**: corpus &lt; ~200K tokens + prompt caching can beat RAG; beyond that, selective top-20 is the economic default ([Anthropic](https://www.anthropic.com/engineering/contextual-retrieval)).

### Latency SLA targets (milliseconds)

> ⚠️ **Gap**: No vendor publishes end-to-end product RAG query **p50/p95/p99** SLAs for a complete retrieve→generate path. Below: published micro-benchmarks + an **[inferred]** stage budget with arithmetic. Treat e2e percentiles as engineering targets, not vendor guarantees.

**Published / estimated stage latencies** ([FAISS](https://github.com/facebookresearch/faiss/wiki/Indexing-1M-vectors); [CalibreOS](https://www.calibreos.com/learn/genai-hybrid-search); [Anthropic](https://www.anthropic.com/engineering/contextual-retrieval)):

| Stage | Latency | Source tag |
| --- | --- | --- |
| FAISS HNSW ANN (in-process, SIFT1M) | **0.01–0.10 ms** | published |
| BM25 top-100 (OpenSearch-class) | **~5–15 ms** | `[engineering estimate]` |
| Dense ANN (HNSW service) | **~10–30 ms** | `[engineering estimate]` |
| Cross-encoder / managed rerank | **~30–80 ms** p50 incl. network | `[engineering estimate]` |
| Retrieval budget inside multi-second answer | **100–200 ms** | `[inferred]` |
| RAG-Fusion multi-query path overhead | ~**0.89 s** | industry report ([RAG Fusion](https://arxiv.org/pdf/2603.02153)) |

**[inferred] latency budget (hybrid + rerank + generate)** — arithmetic for a single-hop enterprise assistant:

| Tier | Target | Composition (additive) | Mitigations |
| --- | --- | --- | --- |
| **p50** | **800 ms** | embed_q 20 + BM25‖ANN 25 + RRF 2 + rerank 50 + TTFT 500 + early tokens ≈ 597 → budget **800** with headroom | Parallel sparse‖dense; skip sequential hybrid; prompt cache; stream tokens |
| **p95** | **2,500 ms** | p50 path + rerank/network jitter + longer decode (~1.5–2 s) | Cap candidates (150→20); stage timeouts; smaller K under load |
| **p99** | **5,000 ms** | fusion/agentic second hop or cold cache; RAG-Fusion-class +0.89 s tails | Deadline propagation; skip rerank if stage &gt; budget; cap `max_depth`; bulkheads per dependency |

\[
T_{\text{retrieval}}^{\text{p50}} \approx \max(T_{\text{BM25}}, T_{\text{ANN}}) + T_{\text{RRF}} + T_{\text{rerank}}
  \approx 25 + 2 + 50 = 77\text{ ms}
\]

Keep retrieval inside **100–200 ms** so LLM TTFT/decode dominate ([CalibreOS](https://www.calibreos.com/learn/genai-hybrid-search)). Elastic notes historical **sequential** hybrid increases latency vs single retriever ([Elastic](https://www.elastic.co/search-labs/blog/improving-information-retrieval-elastic-stack-hybrid)).

### Throughput & back-pressure

| Lever | Design |
| --- | --- |
| **Online QPS** | Size on \(\mathrm{QPS} \times (T_{\text{embed}_q} + T_{\text{ANN/BM25}} + T_{\text{rerank}} + T_{\text{LLM}})\); shard HNSW by tenant or corpus |
| **Ingest** | Batch embeds; token-bucket so ingest cannot starve online query TPM/search quotas **[inferred]** |
| **Back-pressure** | Shed rerank → serve RRF top-K; shed dense → BM25; refuse agentic hops when breaker open |
| **No universal RPM** | Research: no standard published RAG-specific RPM—derive from embedding QPS × dim × shard count |

### NFR: availability, RPO/RTO, compliance, explicit trade-offs

| NFR | Target / posture | Notes |
| --- | --- | --- |
| **Availability** | Query path: multi-AZ ANN+BM25 replicas; generate: multi-region model fallback. Ingest: at-least-once with idempotent chunk keys | Prefer degrade (lexical/abstain) over 5xx hallucination |
| **RPO** | Corpus/ACL in object store + CDC → RPO minutes; index alias lag is the real freshness gap | Nightly ACL sync creates revoke windows ([Truto](https://truto.one/blog/how-to-maintain-document-level-rbac-in-enterprise-rag-pipelines)) |
| **RTO** | Hot replica + alias rollback → minutes; full reindex hours–days depending on \(T_{\text{doc}}\) | Blue/green: keep `vN` until `vN+1` validated |
| **Compliance** | Pre-filter ACLs; PII redact pre-embed; immutable retrieval audit; BAA when PHI; GDPR erase across store **and** indexes ([VaultRAG](https://github.com/AgentPostmortem/VaultRAG); [Scadea](https://scadea.com/rag-security-and-data-governance-access-control-for-retrieved-context/)) | Post-filter fails open (VaultRAG: ACL off → leak **0%→81.8%**) |

**Explicit trade-off — recall vs latency**: Anthropic prefers top-20 after rerank (fail@20 **1.9%**) vs top-5/10; each extra candidate + rerank pass adds tens of ms and tokens. Raising `efSearch` / fusion queries lifts recall/nDCG but inflates **p99** (RAG-Fusion ~**0.89 s** overhead) ([Anthropic](https://www.anthropic.com/engineering/contextual-retrieval); [RAG Fusion](https://arxiv.org/pdf/2603.02153)).

**Explicit trade-off — freshness vs cost**: Continuous re-embed + contextualize at **$1.02/MTok** docs + embed fees buys low RPO; batch nightly reindex lowers $ but serves stale ACL/content until swap.

---

## Part 4 — Distributed Resilience & Security

> ⚠️ **Gap**: Foundational RAG papers/vendor blogs specify index/retrieval far more than Temporal/Kafka orchestration or published breaker thresholds. Durable patterns below bind confirmed index-swap mechanics to **[inferred]** enterprise orchestration ([research §3](../research/06-how-rag-works.md)).

### Durable execution for ingest / index

| Checkpointed state | Why durable | Failure if lost |
| --- | --- | --- |
| Source docs + ACL metadata | Source of truth | Unauthorized or silent stale answers |
| Per-document ingest cursor (offset / etag) | Exactly-once chunking under at-least-once delivery | Duplicate chunks or skipped docs |
| Chunk IDs + content hash + `index_version` | Idempotent upsert | Mixed embedding spaces |
| Embed batch progress (last successful batch id) | Resume without full re-embed | Re-pay embed/contextualize $ |
| BM25 segment flush + ANN build artifact | Replayable index build | Corrupt partial index |
| Alias pointer `live → vN` | Atomic cutover | Readers hit half-built `vN+1` |
| Online audit / retrieval logs | Append-only compliance | Cannot answer “did Y see X?” ([VaultRAG](https://github.com/AgentPostmortem/VaultRAG)) |
| Agentic loop step (optional) | Multi-hop spend control | Duplicate tool calls **[inferred]** |

**Orchestration pattern**: Temporal/Kafka workflow per corpus build—activities: `LoadBatch` → `RedactPII` → `Chunk` → `Contextualize` → `Embed` → `UpsertSparse` → `UpsertDense` → `ValidateRecall` → `AliasSwap`. Workflow replay uses deterministic activity results; poison docs → DLQ; never mix embedding versions in one space. Knowledge updates = **replace non-parametric index**, not retrain generator ([Lewis et al., 2020](https://arxiv.org/abs/2005.11401)).

### Failure taxonomy

| Class | Examples | Handling |
| --- | --- | --- |
| **Transient** | Embed/rerank 429/5xx, ANN timeout, LLM TTFT spike | Retry + exponential backoff + jitter; same idempotency key |
| **Permanent** | Unknown embedding model version, ACL schema invalid, doc parse fatal | Fail activity; do not retry; alert control plane |
| **Poison pill** | One doc always OOMs contextualize / corrupt PDF | Max attempts → DLQ; quarantine doc id; continue corpus |
| **Semantic** | High retrieval, wrong neighborhood; hallucination despite context | CRAG Incorrect → web or abstain; citation checks ([CRAG](https://arxiv.org/html/2401.15884v3); legal RAG still **~17–33%** hallucinate on hard legal ([Stanford legal RAG](https://dho.stanford.edu/wp-content/uploads/Legal_RAG_Hallucinations.pdf))) |
| **Idempotency** | Re-drive ingest after crash | Key `(tenant, doc_id, content_hash, embedding_model, chunk_strategy)` |

### Circuit breaker: closed → open → half-open

Apply **per dependency** (dense ANN, BM25, rerank, generate)—not one breaker for the whole RAG path:

1. **Closed** — traffic flows; count failures / error rate in a sliding window (e.g. 5 failures / 60s **[inferred threshold]**).
2. **Open** — short-circuit that dependency; activate fallback chain; emit telemetry.
3. **Half-open** — after cool-down, allow one probe query; success → closed; failure → open.

### Fallback chains

```
hybrid (BM25 ‖ dense → RRF → rerank)
    → hybrid without rerank (RRF top-K)          # if rerank breaker open / p95 > budget
    → lexical only (BM25)                         # if dense ANN errors (BM25 safe floor)
    → abstain / refuse (or CRAG web if policy)    # if retrieval graded Incorrect / empty ACL set
```

Also: cap agentic iterations; Self-RAG `max_depth` default **6** ([Self-RAG GitHub](https://github.com/akariasai/self-rag)). Prefer abstain over ungrounded generate when evidence fails.

### Zero-Trust MCP for retrieve tools

- Every `retrieve.*` / `embed` / `rerank` call crosses an authenticated MCP (or equivalent) proxy: mTLS or signed service identity; no direct DB credentials in the agent.
- **Retrieved chunk text is untrusted data**, not instructions—delimit in prompts; scan at ingest for prompt-injection ([research §4](../research/06-how-rag-works.md)).
- Network: egress allowlists for embed/rerank/LLM vendors only; deny arbitrary tool fan-out on agentic paths.

### Tool RBAC / ACL-before-ANN

| Control | Rule |
| --- | --- |
| **Tool RBAC** | Agent may call `retrieve.search` with caller’s token; may not call `index.admin` or raw vector upsert |
| **ACL-before-ANN** | Compile `tenant_id` + `allowed_groups` into the vector/BM25 query **before** similarity; never post-trim in app code as the only gate ([AWS Bedrock](https://aws.amazon.com/blogs/machine-learning/access-control-for-vector-stores-using-metadata-filtering-with-knowledge-bases-for-amazon-bedrock/); [VaultRAG](https://github.com/AgentPostmortem/VaultRAG)) |
| **Deny-by-default** | Missing ACL metadata → invisible; VaultRAG: removing ACL predicate → leak **0% → 81.8%** |
| **Live auth** | Resolve groups from IAM per query; avoid stale nightly-only sync revoke windows ([Truto](https://truto.one/blog/how-to-maintain-document-level-rbac-in-enterprise-rag-pipelines)) |

### PII: detect → redact → audit

1. **Detect** — NER/Presidio/Comprehend on full documents **before** chunk+embed ([Scadea](https://scadea.com/rag-security-and-data-governance-access-control-for-retrieved-context/)).
2. **Redact** — replace with stable tokens in indexed text; sealed mapping vault for authorized re-identification.
3. **Audit** — append-only `{correlation_id, principal, doc_ids, pii_tokens_redacted, action}` to immutable store; BAA when PHI.

### Immutable logs / chain-of-custody

Structured audit must answer `"did user Y ever see doc X?"`: principal, query hash, filter decision, chunk IDs returned/denied, model version, index version—WORM / hash-chained ([VaultRAG](https://github.com/AgentPostmortem/VaultRAG); [Unolabs](https://unolabs.com/blog/security-frameworks-for-ai)). GDPR erasure = coordinated delete across object store **and** BM25/ANN.

---

## Part 5 — Production Enterprise Code

Runnable hybrid retrieve+generate loop: retries + jitter, circuit breaker, fallback chain, correlation IDs, graceful degradation. Deterministic fake embedder/generator. No API keys; no unfinished stubs.

```python
#!/usr/bin/env python3
"""Hybrid RAG retrieve+generate with enterprise resilience primitives."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import random
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Sequence


# ---------------------------------------------------------------------------
# Structured logging with correlation IDs
# ---------------------------------------------------------------------------

class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.time(),
            "level": record.levelname,
            "msg": record.getMessage(),
            "logger": record.name,
            "correlation_id": getattr(record, "correlation_id", None),
            "tenant_id": getattr(record, "tenant_id", None),
            "stage": getattr(record, "stage", None),
            "attempt": getattr(record, "attempt", None),
            "breaker": getattr(record, "breaker", None),
            "fallback": getattr(record, "fallback", None),
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


LOG = build_logger("rag.pipeline")


# ---------------------------------------------------------------------------
# Failure taxonomy + retries with exponential backoff and jitter
# ---------------------------------------------------------------------------

class FailureKind(str, Enum):
    TRANSIENT = "transient"
    PERMANENT = "permanent"


class RagError(Exception):
    def __init__(self, message: str, kind: FailureKind) -> None:
        super().__init__(message)
        self.kind = kind


def retry_with_jitter(
    fn: Callable[[], Any],
    *,
    correlation_id: str,
    stage: str,
    max_attempts: int = 4,
    base_delay_s: float = 0.01,
    max_delay_s: float = 0.08,
) -> Any:
    last: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except RagError as exc:
            last = exc
            LOG.warning(
                "stage_failed",
                extra={
                    "correlation_id": correlation_id,
                    "stage": stage,
                    "attempt": attempt,
                },
            )
            if exc.kind == FailureKind.PERMANENT or attempt == max_attempts:
                raise
            delay = min(max_delay_s, base_delay_s * (2 ** (attempt - 1)))
            delay = delay * (0.5 + random.random())  # full jitter in [0.5, 1.5)x
            time.sleep(delay)
    assert last is not None
    raise last


# ---------------------------------------------------------------------------
# Circuit breaker: closed → open → half-open
# ---------------------------------------------------------------------------

class BreakerState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    name: str
    failure_threshold: int = 3
    cooldown_s: float = 0.05
    failures: int = 0
    state: BreakerState = BreakerState.CLOSED
    opened_at: float = 0.0

    def allow(self) -> bool:
        if self.state == BreakerState.CLOSED:
            return True
        if self.state == BreakerState.OPEN:
            if time.time() - self.opened_at >= self.cooldown_s:
                self.state = BreakerState.HALF_OPEN
                return True
            return False
        return True  # HALF_OPEN: single probe

    def record_success(self) -> None:
        self.failures = 0
        self.state = BreakerState.CLOSED

    def record_failure(self) -> None:
        self.failures += 1
        if self.state == BreakerState.HALF_OPEN or self.failures >= self.failure_threshold:
            self.state = BreakerState.OPEN
            self.opened_at = time.time()


# ---------------------------------------------------------------------------
# Deterministic fake embedder / corpus / generator (no network, no keys)
# ---------------------------------------------------------------------------

def fake_embed(text: str, dim: int = 16) -> list[float]:
    """Deterministic bag-of-tokens embedding in R^dim."""
    vec = [0.0] * dim
    for tok in text.lower().split():
        h = int(hashlib.sha256(tok.encode()).hexdigest(), 16)
        vec[h % dim] += 1.0
        vec[(h // dim) % dim] += 0.37
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    tenant_id: str
    allowed_groups: frozenset[str]
    text: str
    terms: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        object.__setattr__(self, "terms", frozenset(self.text.lower().split()))


@dataclass
class Hit:
    chunk: Chunk
    score: float
    rank: int


class HybridIndex:
    def __init__(self, chunks: list[Chunk]) -> None:
        self.chunks = chunks
        self.vectors = {c.chunk_id: fake_embed(c.text) for c in chunks}

    def _acl_ok(self, chunk: Chunk, tenant_id: str, groups: set[str]) -> bool:
        if chunk.tenant_id != tenant_id:
            return False
        if not chunk.allowed_groups:
            return False  # deny-by-default
        return bool(chunk.allowed_groups & groups)

    def bm25ish(self, query: str, tenant_id: str, groups: set[str], k: int) -> list[Hit]:
        q = set(query.lower().split())
        scored: list[tuple[float, Chunk]] = []
        for c in self.chunks:
            if not self._acl_ok(c, tenant_id, groups):
                continue
            overlap = len(q & c.terms)
            if overlap:
                scored.append((float(overlap), c))
        scored.sort(key=lambda x: (-x[0], x[1].chunk_id))
        return [Hit(chunk=c, score=s, rank=i + 1) for i, (s, c) in enumerate(scored[:k])]

    def dense(
        self,
        query: str,
        tenant_id: str,
        groups: set[str],
        k: int,
        *,
        fail: bool = False,
    ) -> list[Hit]:
        if fail:
            raise RagError("ann_unavailable", FailureKind.TRANSIENT)
        qv = fake_embed(query)
        scored: list[tuple[float, Chunk]] = []
        for c in self.chunks:
            if not self._acl_ok(c, tenant_id, groups):
                continue
            scored.append((cosine(qv, self.vectors[c.chunk_id]), c))
        scored.sort(key=lambda x: (-x[0], x[1].chunk_id))
        return [Hit(chunk=c, score=s, rank=i + 1) for i, (s, c) in enumerate(scored[:k])]


def rrf_fuse(lists: list[list[Hit]], k_rrf: int = 60, top_k: int = 3) -> list[Hit]:
    scores: dict[str, float] = {}
    by_id: dict[str, Chunk] = {}
    for hits in lists:
        for h in hits:
            scores[h.chunk.chunk_id] = scores.get(h.chunk.chunk_id, 0.0) + 1.0 / (
                k_rrf + h.rank
            )
            by_id[h.chunk.chunk_id] = h.chunk
    ordered = sorted(scores.items(), key=lambda x: (-x[1], x[0]))[:top_k]
    return [
        Hit(chunk=by_id[cid], score=sc, rank=i + 1) for i, (cid, sc) in enumerate(ordered)
    ]


def fake_generate(query: str, contexts: list[Chunk]) -> str:
    if not contexts:
        return "ABSTAIN: no authorized evidence."
    cites = ", ".join(c.chunk_id for c in contexts)
    snippet = contexts[0].text[:80]
    return f"ANSWER(query={query!r}; cites=[{cites}]): {snippet}"


# ---------------------------------------------------------------------------
# Pipeline: hybrid → lexical → abstain, with breakers + graceful degradation
# ---------------------------------------------------------------------------

@dataclass
class RagResult:
    answer: str
    mode: str
    chunk_ids: list[str]
    degraded: bool
    correlation_id: str


class RagPipeline:
    def __init__(self, index: HybridIndex) -> None:
        self.index = index
        self.dense_breaker = CircuitBreaker("dense_ann")
        self._dense_fail_inject = 0

    def inject_dense_failures(self, n: int) -> None:
        self._dense_fail_inject = n

    def run(
        self,
        query: str,
        *,
        tenant_id: str,
        groups: set[str],
        correlation_id: str | None = None,
    ) -> RagResult:
        cid = correlation_id or str(uuid.uuid4())
        LOG.info(
            "query_start",
            extra={"correlation_id": cid, "tenant_id": tenant_id, "stage": "start"},
        )

        # Lexical always attempted (safe floor).
        lexical = retry_with_jitter(
            lambda: self.index.bm25ish(query, tenant_id, groups, k=5),
            correlation_id=cid,
            stage="bm25",
        )

        dense_hits: list[Hit] = []
        degraded = False
        mode = "hybrid"

        if self.dense_breaker.allow():
            def _dense() -> list[Hit]:
                fail = self._dense_fail_inject > 0
                if fail:
                    self._dense_fail_inject -= 1
                return self.index.dense(
                    query, tenant_id, groups, k=5, fail=fail
                )

            try:
                dense_hits = retry_with_jitter(
                    _dense, correlation_id=cid, stage="dense", max_attempts=3
                )
                self.dense_breaker.record_success()
            except RagError:
                self.dense_breaker.record_failure()
                degraded = True
                mode = "lexical"
                LOG.warning(
                    "dense_degraded",
                    extra={
                        "correlation_id": cid,
                        "breaker": self.dense_breaker.state.value,
                        "fallback": "lexical",
                        "stage": "dense",
                    },
                )
        else:
            degraded = True
            mode = "lexical"
            LOG.warning(
                "dense_short_circuit",
                extra={
                    "correlation_id": cid,
                    "breaker": self.dense_breaker.state.value,
                    "fallback": "lexical",
                    "stage": "dense",
                },
            )

        if dense_hits and lexical:
            fused = rrf_fuse([lexical, dense_hits], top_k=3)
            mode = "hybrid"
        elif dense_hits:
            fused = dense_hits[:3]
            mode = "dense"
        else:
            fused = lexical[:3]
            mode = "lexical" if lexical else "abstain"
            degraded = degraded or not lexical

        if not fused:
            answer = fake_generate(query, [])
            LOG.info(
                "abstain",
                extra={
                    "correlation_id": cid,
                    "fallback": "abstain",
                    "stage": "generate",
                },
            )
            return RagResult(
                answer=answer,
                mode="abstain",
                chunk_ids=[],
                degraded=True,
                correlation_id=cid,
            )

        contexts = [h.chunk for h in fused]
        answer = fake_generate(query, contexts)
        LOG.info(
            "query_ok",
            extra={
                "correlation_id": cid,
                "tenant_id": tenant_id,
                "stage": "generate",
                "fallback": mode,
            },
        )
        return RagResult(
            answer=answer,
            mode=mode,
            chunk_ids=[c.chunk_id for c in contexts],
            degraded=degraded,
            correlation_id=cid,
        )


def demo() -> None:
    chunks = [
        Chunk(
            "c1",
            "t1",
            frozenset({"eng"}),
            "Error code TS-999 means token budget exceeded on embed batch",
        ),
        Chunk(
            "c2",
            "t1",
            frozenset({"eng"}),
            "Hybrid retrieval fuses BM25 lexical ranks with dense ANN scores via RRF",
        ),
        Chunk(
            "c3",
            "t1",
            frozenset({"hr"}),
            "Employee SSN 123-45-6789 must be redacted before indexing",
        ),
        Chunk(
            "c4",
            "t2",
            frozenset({"eng"}),
            "Other tenant secret runbook — must never appear for t1",
        ),
    ]
    pipe = RagPipeline(HybridIndex(chunks))
    cid = "corr-demo-001"

    r1 = pipe.run(
        "What does error code TS-999 mean?",
        tenant_id="t1",
        groups={"eng"},
        correlation_id=cid,
    )
    print(r1)

    pipe.inject_dense_failures(5)
    r2 = pipe.run(
        "How does hybrid retrieval fuse ranks?",
        tenant_id="t1",
        groups={"eng"},
        correlation_id="corr-demo-002",
    )
    print(r2)

    r3 = pipe.run(
        "SSN policy",
        tenant_id="t1",
        groups={"eng"},
        correlation_id="corr-demo-003",
    )
    print(r3)  # ACL: eng cannot see hr chunk → abstain or non-SSN lexical miss


if __name__ == "__main__":
    random.seed(0)
    demo()
```

Run: `python3 modules/06-how-rag-works.md` is not valid—copy the code block to `rag_pipeline_demo.py` or extract with your usual snippet runner. Self-check: `python3 -c` after paste runs `demo()` and prints hybrid then degraded lexical results with JSON logs on stderr.

---

## Part 6 — Architectural System Design Scenarios

Exactly **two** scenarios. Interview prompts only after both.

### Scenario 1 — Internal knowledge assistant (50K–500K docs)

**Problem**: Design an internal assistant over engineering wikis, runbooks, and ticket macros for **~5k employees**, **50K–500K docs**, target **fail@20 ≤ 2%** on gold questions, **p95 e2e ≤ 2.5 s** **[inferred SLA]**, citations required, department ACLs enforced.

**Proposed architecture** (component diagram — not Part 1):

```
  ┌────────────┐   ┌─────────────────┐   ┌──────────────────────────────┐
  │ SSO / IAM  ├──►│ Query Gateway   ├──►│ Retrieval Workers            │
  └────────────┘   │  filter compile │   │  ┌────────┐    ┌──────────┐  │
                   │  deadline budg. │   │  │ BM25   │──┐ │ HNSW ANN │  │
                   └────────┬────────┘   │  └────────┘  │ └──────────┘  │
                            │            │       └──────┼──► RRF ──► Rerank
                            │            └──────────────┼───────────────┘
                            │                           ▼
                   ┌────────▼────────┐         ┌────────────────┐
                   │ Prompt+Cite LLM │◄────────│ Context packer │
                   └────────┬────────┘         │ (edge-place K) │
                            │                  └────────────────┘
         ┌──────────────────┼──────────────────┐
         ▼                  ▼                  ▼
  ┌─────────────┐   ┌─────────────┐   ┌──────────────────┐
  │ Wiki Object │   │ Index vN    │   │ Audit WORM log   │
  │ Store       │   │ alias live  │   │ chunk IDs+user   │
  └─────────────┘   └─────────────┘   └──────────────────┘
```

**Technology choices**: hybrid BM25+dense → RRF → cross-encoder/Cohere to **10–20** chunks; optional contextual embeddings at index time; metadata filters for department ACLs; FAISS/OpenSearch-class HNSW; prompt-cache system prefix ([Anthropic](https://www.anthropic.com/engineering/contextual-retrieval); [Elastic](https://www.elastic.co/search-labs/blog/improving-information-retrieval-elastic-stack-hybrid)).

**Trade-off matrix**

| Approach | Cost | Latency | Ops | Security | Scalability |
| --- | --- | --- | --- | --- | --- |
| **A. Dense-only top-k** | Low $/query | Best p50 | Low (one index) | Needs ACL prefilter | Easy to shard; weak on `TS-999`-class IDs |
| **B. Hybrid + RRF + rerank (recommended)** | Med ($~41/1k **[assumed]** rates) | Med; +30–80 ms rerank | Med (dual index + rerank SLO) | Filter-before-ANN; audit cites | Scales with shard+alias; K=20 recall path |
| **C. Stuff ≤200K tokens + cache** | Low when warm cache | Best TTFT when cached | Low | ACL = prompt isolation only | **Fails** past ~200K tok / window; lost-in-the-middle |

**Decision rationale**: Choose **B**. Anthropic stack shows fail@20 **5.7% → 1.9%** with contextual hybrid + rerank; Elastic shows hybrid nDCG gains via RRF without per-query weight tuning; corpus size exceeds stuffing’s ~200K-token regime ([Anthropic](https://www.anthropic.com/engineering/contextual-retrieval); [Elastic](https://www.elastic.co/search-labs/blog/improving-information-retrieval-elastic-stack-hybrid)). Capacity sketch **[inferred]**: 1M chunks × 1536-d float32 ≈ 6 GB vectors before HNSW graph (~\(8M\) bytes/vector) ([FAISS guidelines](https://github.com/facebookresearch/faiss/wiki/Guidelines-to-choose-an-index)).

---

### Scenario 2 — Regulated multi-tenant SaaS knowledge API

**Problem**: Multi-tenant SaaS must answer in-product help from **per-customer** knowledge bases (PHI/PII possible), **10k tenants**, strict “no cross-tenant chunk ever”, SOC2 audit of every retrieval, p99 dominated by safety over raw speed, ingest continuous via customer uploads.

**Proposed architecture** (component diagram — not Part 1):

```
                    ┌────────────────────────────────────────┐
                    │         CONTROL: Tenant Policy         │
                    │  IAM groups · retention · BAA flags    │
                    └─────────────┬──────────────────────────┘
                                  │
         upload                   ▼
  ┌──────────────┐    ┌─────────────────────┐    ┌─────────────────────┐
  │ Customer Doc │───►│ Ingest Workflow     │───►│ Per-tenant index    │
  │ Landing Zone │    │ detect→redact→audit │    │ OR shared+forced    │
  └──────────────┘    │ chunk→embed→build   │    │ tenant prefilter    │
                      └──────────┬──────────┘    └──────────┬──────────┘
                                 │                          │
                      ┌──────────▼──────────┐    ┌──────────▼──────────┐
                      │ MCP retrieve proxy  │◄───│ Query: ACL∩tenant   │
                      │ tool RBAC + mTLS    │    │ BEFORE ANN/BM25     │
                      └──────────┬──────────┘    └─────────────────────┘
                                 ▼
                      ┌─────────────────────┐
                      │ Generate (no tools) │──► Immutable audit lane
                      │ delimit untrusted   │
                      └─────────────────────┘
```

**Technology choices**: PII redaction pre-embed; mandatory `tenant_id` (+ row ACL) prefilters; MCP Zero-Trust retrieve tools; immutable WORM audit; blue/green per-tenant or shared-sharded indexes; CRAG-style abstain over web search unless contract allows ([AWS Bedrock](https://aws.amazon.com/blogs/machine-learning/access-control-for-vector-stores-using-metadata-filtering-with-knowledge-bases-for-amazon-bedrock/); [VaultRAG](https://github.com/AgentPostmortem/VaultRAG); [Scadea](https://scadea.com/rag-security-and-data-governance-access-control-for-retrieved-context/)).

**Trade-off matrix**

| Approach | Cost | Latency | Ops | Security | Scalability |
| --- | --- | --- | --- | --- | --- |
| **A. Shared index + app post-filter** | Lowest density $ | Low | Low | **Unsafe** (VaultRAG leak path) | High density; fails compliance |
| **B. Shared index + mandatory prefilter (recommended for density)** | Low–med | Low–med (filter selectivity) | Med (filter correctness tests) | Strong if deny-by-default + live IAM | High; one cluster, tenant tag |
| **C. Per-tenant isolated indexes** | Highest (idle shards) | Predictable noisy-neighbor isolation | High (N× builds, aliases) | Strongest isolation / blast radius | Ops ceiling at 10k tenants unless pooled |

**Decision rationale**: Default **B** with continuous ACL correctness tests and WORM audit; escalate hot/regulated tenants to **C** (isolated indexes) when blast-radius or residency requires it. Never ship **A**—post-filter is a known fail-open. Prefer abstain over web fallback when PHI/BAA scope forbids egress. Pair with detect→redact→audit on ingest and GDPR delete across store+indexes.

---

### Interview prompts (after both scenarios)

1. Walk the dual-pipeline topology: what is checkpointed on ingest alias swap, and how do you prevent mixed embedding-model reads?
2. Derive `$ / 1k queries` for top-5 vs top-20 vs stuffing 200K with and without 90% prompt cache; state every price assumption.
3. Given only stage microbenchmarks (no vendor e2e percentiles), build a p50/p95/p99 budget and show where you shed rerank vs dense vs abstain.
4. Why must ACL filters run **before** ANN? Quantify the VaultRAG-style failure mode.
5. Compare Self-RAG vs CRAG vs naive RAG on control structure, cost, and p99; where do you set `max_depth`?
6. How do you mitigate lost-in-the-middle without blowing the token budget?
7. Design the hybrid → lexical → abstain breaker chain with half-open probes per dependency.
8. When would you choose long-context stuffing over RAG, and when does Liu et al. make stuffing unsafe even inside the window?

---

## Sources (selected)

- [Lewis et al., 2020](https://arxiv.org/abs/2005.11401) — RAG formalization, Wikipedia index scale, EM results  
- [Anthropic Contextual Retrieval](https://www.anthropic.com/engineering/contextual-retrieval) — fail@20 stack, $1.02/MTok, top-20, caching  
- [Elastic hybrid retrieval](https://www.elastic.co/search-labs/blog/improving-information-retrieval-elastic-stack-hybrid) — RRF / nDCG@10  
- [Liu et al., 2024](https://arxiv.org/abs/2307.03172) — lost-in-the-middle  
- [Self-RAG](https://proceedings.iclr.cc/paper_files/paper/2024/file/25f7be9694d7b32d5cc670927b8091e1-Paper-Conference.pdf) · [CRAG](https://arxiv.org/html/2401.15884v3) — agentic control  
- [VaultRAG](https://github.com/AgentPostmortem/VaultRAG) · [AWS Bedrock filtering](https://aws.amazon.com/blogs/machine-learning/access-control-for-vector-stores-using-metadata-filtering-with-knowledge-bases-for-amazon-bedrock/) — ACL-before-ANN  
- Full bibliography: `research/06-how-rag-works.md`
