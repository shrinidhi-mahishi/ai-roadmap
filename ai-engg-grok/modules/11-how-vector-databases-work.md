# Module 11 — How Vector Databases Work

**Audience**: Principal AI Architect interview prep · personal deep study  
**Sequence**: 11 (index/store layer after RAG application patterns in 06 and agent memory in 10)  
**Grounded in**: `research/11-how-vector-databases-work.md` (22 sources, 2026-09-30)

Vector databases answer **similarity over high-dimensional embeddings**, not equality/range on keys. Relational indexes (`B-tree`, hash, GiST) fail here because distance/inner-product spans the whole vector — without an ANN structure the engine scans **O(n)** candidates ([System Design Newsletter — What is a Vector Database](https://newsletter.systemdesign.one/p/what-is-a-vector-database)). This module covers the **index/store layer only**: HNSW/IVF/PQ, filtering semantics, durability, tenancy, and ops knobs. Chunking, contextual retrieval, and prompt assembly live in topic 06.

---

## Part 1 — System Topology & Data Flow

### Architecture map

```
                         ┌──────────────────────────────────────────────────────────┐
                         │                    CONTROL PLANE                         │
                         │  collection/index lifecycle · dim · metric · named vecs  │
                         │  HNSW/IVF params (M, efConstruction, nlist) · payload    │
                         │  index defs · tenancy/namespace policy · API keys/JWT    │
                         │                                                          │
                         │  ┌────────────┐  ┌────────────┐  ┌────────────────────┐  │
                         │  │ Schema /   │  │ Optimizer  │  │ Auth / tenancy     │  │
                         │  │ hnsw_cfg   │  │ reindex    │  │ namespaces · RLS   │  │
                         │  └─────┬──────┘  └─────┬──────┘  └─────────┬──────────┘  │
                         └────────┼───────────────┼───────────────────┼─────────────┘
                                  │               │                   │
                                  ▼               ▼                   ▼
                         ┌──────────────────────────────────────────────────────────┐
                         │                     DATA PLANE                           │
                         │  upsert → durable log → memtable/segment                 │
                         │  query: embed → filter → ANN → hydrate top-k            │
                         │  write routers · query executors · slab/segment merge    │
                         └───┬──────────────────────┼───────────────────────┬───────┘
                             │                      │                       │
              ┌──────────────┴───────┐  ┌───────────┴─────────┐  ┌──────────┴───────┐
              │     TOOL PROXIES     │  │    PERSISTENCE      │  │    TELEMETRY     │
              ├──────────────────────┤  ├─────────────────────┤  ├──────────────────┤
              │  retrieve MCP tool   │  │  request log / WAL  │  │  QPS · p95 ms    │
              │  upsert / delete API │  │  memtable → slabs   │  │  recall@k proxy  │
              │  hybrid RRF fuse     │  │  object store cold  │  │  filter select.  │
              │  gateway tenant inj. │  │  HNSW / IVF / PQ    │  │  breaker · corr. │
              └──────────────────────┘  └─────────────────────┘  └──────────────────┘
```

**Plane responsibilities**

| Plane | Role in a vector store | Concrete components |
| --- | --- | --- |
| **CONTROL PLANE** | Index/collection lifecycle, schema (dim, metric, named vectors), ANN params, payload index defs, tenancy policy, auth keys | Pinecone global control plane; Qdrant collection + `hnsw_config` + payload indexes; Postgres DDL for `pgvector` ([Pinecone architecture](https://docs.pinecone.io/guides/get-started/database-architecture); [Qdrant Indexing](https://qdrant.tech/documentation/manage-data/indexing/); [pgvector README](https://github.com/pgvector/pgvector)) |
| **DATA PLANE** | Upsert → durable log → memtable/segment; query routers/executors; ANN ± filter → top-k merge | Pinecone regional write path vs query executors; FAISS in-process; Qdrant segments; Postgres heap + HNSW/IVFFlat ([Pinecone architecture](https://docs.pinecone.io/guides/get-started/database-architecture); [FAISS wiki](https://github.com/facebookresearch/faiss/wiki)) |
| **PERSISTENCE** | Raw/quantized vectors + metadata; RAM/SSD/object tiers; WAL/request log; immutable slabs | Pinecone LSN log → memtable → slabs; Postgres WAL; Qdrant segments + payload tiers `pinned`/`cached`/`cold` ([Pinecone serverless blog](https://www.pinecone.io/blog/serverless-architecture/); [Qdrant Indexing](https://qdrant.tech/documentation/manage-data/indexing/)) |
| **TOOL PROXIES** | Agent-facing retrieve/upsert tools, hybrid fuse, gateway-enforced tenant filters | MCP `retrieve` tool; Pinecone/Qdrant/Weaviate clients; app gateway that injects `tenant_id` ([Qdrant Multitenancy](https://qdrant.tech/documentation/manage-data/multitenancy/)) |
| **TELEMETRY** | Query latency, QPS, filter selectivity, recall proxies, breaker state, correlation IDs | Cloud metrics + app spans; rate-limit counters ([Pinecone architecture](https://docs.pinecone.io/guides/get-started/database-architecture)) |

### Three layers inside the store

1. **Storage** — vectors + metadata; durability and quantization.  
2. **Index** — ANN structure (HNSW graph, IVF lists, PQ codes) trading recall for sublinear search.  
3. **Query** — accept/embed query vector → filter × ANN → distance rank → top-k ([System Design Newsletter](https://newsletter.systemdesign.one/p/what-is-a-vector-database)).

### End-to-end request-flow narrative

**Online query path (embed → filter → ANN → hydrate)**

1. **Ingress** — Client or MCP `retrieve` tool sends query text (or precomputed vector), `top_k`, and metadata predicates. CONTROL PLANE has already fixed dim/metric/index params; gateway attaches **mandatory** tenant scope before the call leaves the proxy ([Qdrant Multitenancy](https://qdrant.tech/documentation/manage-data/multitenancy/); [Pinecone architecture](https://docs.pinecone.io/guides/get-started/database-architecture)).
2. **Embed** — If the request carries text, the application (or an embed sidecar) produces a query vector matching the corpus model/dim/metric. Dimension or metric mismatch yields systematic garbage neighbors ([pgvector README](https://github.com/pgvector/pgvector)).
3. **Filter** — Metadata/ACL predicates become a bitmap, allow-list, or post-filter set depending on engine:
   - **Pre-filter style**: Weaviate inverted allow-list before HNSW admits IDs; Pinecone per-slab metadata bitmaps adapt by selectivity ([Weaviate filtering](https://archive.docs.weaviate.io/weaviate/concepts/filtering); [Pinecone ICML 2025](https://www.pinecone.io/research/ICML_2025.pdf)).
   - **Filter-aware graph**: Qdrant filterable HNSW adds payload edges; planner chooses HNSW vs payload-index+rescore ([Qdrant filtering guide](https://qdrant.tech/documentation/search-patterns/vector-search-filtering/)).
   - **Post-filter**: pgvector applies predicates **after** ANN — selective filters under-fill top-k unless iterative scans / partial indexes run ([pgvector README](https://github.com/pgvector/pgvector)).
4. **ANN** — Traversal (HNSW `efSearch`, IVF `nprobe`) produces approximate neighbors among admitted IDs. Fresh Pinecone writes are visible from the **memtable** before slab flush; query merges slab executors + memtable and applies tombstones ([Pinecone architecture](https://docs.pinecone.io/guides/get-started/database-architecture)).
5. **Hydrate** — Rank by distance/IP/cosine; return top-k IDs + payloads (and optionally full vectors for re-rank). Hybrid paths may prefetch dense+sparse and fuse with RRF in-engine before hydrate ([Qdrant hybrid](https://qdrant.tech/course/beginners/module-3/hybrid-search-in-qdrant/); [Pinecone hybrid](https://docs.pinecone.io/guides/search/hybrid-search)).
6. **TELEMETRY** — Emit stage timers (embed RTT, filter, ANN, hydrate), selectivity, correlation id; feed circuit breakers in the tool proxy.

**Write path (brief)**: upsert → durable request log / WAL with LSN → 200 OK → background index builder; deletes via tombstones + compaction (LSM-like on Pinecone) ([Pinecone architecture](https://docs.pinecone.io/guides/get-started/database-architecture); [ICML 2025](https://www.pinecone.io/research/ICML_2025.pdf)).

---

## Part 2 — Core Mechanics & Algorithms

### Why exact scan collapses

Illustrative unit cost (newsletter, not a vendor SLA): at **0.0001 s** per distance, **10k** chunks → **~1 s/query**; **10M** → **~1,000 s**. At **100 QPS** the system saturates. A float32 **1,536-d** vector is \(1536 × 4 = 6{,}144\) bytes (**~6 KB**) before graph overhead ([System Design Newsletter](https://newsletter.systemdesign.one/p/what-is-a-vector-database)).

**Heuristic**: pgvector often enough below **~100k** vectors and moderate QPS; millions + tight latency → dedicated engines ([System Design Newsletter](https://newsletter.systemdesign.one/p/what-is-a-vector-database)).

### HNSW (primary production ANN)

Paper: Malkov & Yashunin, *Efficient and robust approximate nearest neighbor search using Hierarchical Navigable Small World graphs* ([arXiv:1603.09320](https://arxiv.org/abs/1603.09320)).

- Multi-layer proximity graphs; max layer drawn from an **exponentially decaying** distribution; upper layers sparse long-range, layer 0 dense.
- Search: top entry → greedy descend → expand layer 0 with candidate list size **`ef`** (`efSearch`).
- Build params: **`M`** (max edges; paper range **5–48**), **`Mmax0 ≈ 2M`**, **`efConstruction`** (paper notes **100** builds usable index on 10M SIFT in ~3 min on their 4×10-core Xeon), **`mL`** level normalization.
- Neighbor-selection **heuristic** (directional diversity) beats naïve closest-M on clustered data ([HNSW paper](https://arxiv.org/abs/1603.09320)).

**Complexity (paper, idealized Delaunay assumptions)**: expected hops/layer ≈ constant → **logarithmic search** in \(N\); construction **\(O(N \log N)\)** in relatively low-\(d\) regimes. High-\(d\) remains empirically strong; strict analysis does not fully carry over ([HNSW paper §4.2](https://arxiv.org/abs/1603.09320)).

**Faiss `IndexHNSWFlat` memory**: **`4d + x·M·2·4` bytes/vector** (floats + graph); Flat is **`4d`** ([FAISS indexes](https://github.com/facebookresearch/faiss/wiki/Faiss-indexes)). More **`M` / `ef`** → better recall, more RAM, slower queries.

### IVF + product quantization

- **IVF**: partition into **`nlist`** cells; query visits **`nprobe`** lists. Rule of thumb: **`nlist ≈ C · √n`** (\(C\) ~10 order). Fraction scanned ≈ **`nprobe/nlist`** (underestimate if unbalanced) ([FAISS indexes](https://github.com/facebookresearch/faiss/wiki/Faiss-indexes)).
- **PQ** (Jégou et al.): split into \(M\) subvectors coded in few bits — Faiss `IVFx,PQ…` stores **`ceil(M·nbits/8)+8` bytes/vector** vs **`4d`** Flat ([FAISS indexes](https://github.com/facebookresearch/faiss/wiki/Faiss-indexes)).
- **SQ8**: **`d` bytes** vs Flat **`4d`** (~**4×** smaller) ([FAISS indexes](https://github.com/facebookresearch/faiss/wiki/Faiss-indexes); [Newsletter](https://newsletter.systemdesign.one/p/what-is-a-vector-database)).

### pgvector knobs

Exact NN without ANN index (perfect recall). Approximate: **HNSW** (better speed–recall, slower build) vs **IVFFlat** (faster build, needs list training). Defaults commonly shown: HNSW `m=16`, `ef_construction=64`, query `hnsw.ef_search` default **40**; IVFFlat start `lists`, probes ≈ **√lists**. Dim caps with indexes: `vector` ≤ 2,000 ([pgvector README](https://github.com/pgvector/pgvector)).

### Filtering × ANN (architect invariant)

| Engine | Interaction | Failure if ignored |
| --- | --- | --- |
| **pgvector** | Post-filter after ANN; iterative scans optional | 10% match rate × `ef_search=40` → ~**4** survivors → under-filled top-k ([pgvector README](https://github.com/pgvector/pgvector)) |
| **Qdrant** | Filterable HNSW + payload indexes; ACORN for disconnect | Soft-deletes / multi-filter strand navigation ([Qdrant Indexing](https://qdrant.tech/documentation/manage-data/indexing/)) |
| **Weaviate** | Pre-filter allow-list; `acorn` vs `sweeping`; `flatSearchCutoff` default **40,000** | Restrictive filters fall back to flat ([Weaviate vector index](https://docs.weaviate.io/weaviate/config-refs/indexing/vector-index)) |
| **Pinecone serverless** | Adaptive pre/mid/bypass via metadata bitmaps | Naïve filtered IVF recall cliffs under varying selectivity ([ICML 2025](https://www.pinecone.io/research/ICML_2025.pdf)) |

### Key invariants

1. Query and corpus share **embedding model, dimensionality, and metric**.  
2. Soft tenancy is only safe if **every** query injects the tenant predicate (payload filter ≠ RBAC).  
3. ANN + SQ/PQ intentionally trade recall — Faiss HNSW R@1 at `efSearch=16` is **0.874**, not 1.0 ([FAISS Indexing 1M](https://github.com/facebookresearch/faiss/wiki/Indexing-1M-vectors)).

---

## Part 3 — Token Economics & NFR Analysis

> Vector indexes are **not token-metered**. Dollar cost is **embedding API spend + RAM/storage/QPS infra**. Embedding token spend is an application concern (topic 06); formulas below keep it adjacent so architects can budget `$ per 1k queries`.

### Cost formula: `$ per 1k queries`

Define **1 query** = one online retrieve (embed query text → filter → ANN → hydrate). Generation tokens are **out of scope** here.

**Labeled price assumptions (verify live SKUs before budgeting)**

| Parameter | Value | Label |
| --- | --- | --- |
| Embed model | OpenAI `text-embedding-3-small` class | **[assumed]** illustrative mid-2020s aggregator rate |
| Embed price | **$0.02 / MTok** | **[assumed]** same class as topic-06 listing; re-check vendor card |
| Query text tokens | \(T_q = 50\) | **[assumed]** short user/agent query |
| Index infra | amortized \$/GB-month × working set | **[inferred]** from memory arithmetic below — not a vendor SKU |

\[
\begin{aligned}
C_{\text{embed}/1k} &= 1000 \times \frac{T_q}{10^6} \times \$0.02
  = 1000 \times \frac{50}{10^6} \times 0.02 = \$0.001 \\
C_{\text{index}/1k} &= \frac{\text{monthly RAM/storage \$ for hot working set}}{\text{queries per month}} \times 1000 \\
C_{\$/\text{1k queries}} &= C_{\text{embed}/1k} + C_{\text{index}/1k}
\end{aligned}
\]

Embedding API is often **negligible** vs index RAM at scale; the index bill dominates once \(N\) is large.

### Index memory cost (from research)

| Item | Value | Source |
| --- | --- | --- |
| float32 1536-d | **6,144 B ≈ 6 KB**/vector | \(1536×4\); newsletter ~**6 KB** ([System Design Newsletter](https://newsletter.systemdesign.one/p/what-is-a-vector-database)) |
| Scalar quant 32→8 (SQ8) | **~4×** smaller (`d` bytes vs `4d`) | [FAISS indexes](https://github.com/facebookresearch/faiss/wiki/Faiss-indexes) |
| PQ codes | **`ceil(M·nbits/8)+8` B**/vec | [FAISS indexes](https://github.com/facebookresearch/faiss/wiki/Faiss-indexes) |
| HNSW Flat | **`4d + O(M)`** link overhead | [FAISS indexes](https://github.com/facebookresearch/faiss/wiki/Faiss-indexes) |

**Capacity sketch [inferred from Faiss formulas]**: \(N=10^7\), \(d=1536\), \(M=16\) → vectors alone ≈ **61.4 GB**; graph adds several GB more before replicas. Quantize (SQ/PQ) or IVF-PQ when this exceeds node RAM ([FAISS indexes](https://github.com/facebookresearch/faiss/wiki/Faiss-indexes); [Newsletter](https://newsletter.systemdesign.one/p/what-is-a-vector-database)).

### Index-only latency (published Faiss SIFT1M)

Faiss **`IndexHNSWFlat` on SIFT1M** (20 threads, batch-friendly; wiki warns single-thread ~**5–12×** slower) ([FAISS Indexing 1M](https://github.com/facebookresearch/faiss/wiki/Indexing-1M-vectors)):

| `efSearch` | ms/query (**index-only**) | R@1 |
| --- | --- | --- |
| **16** | **0.011** | 0.8740 |
| 32 | 0.020 | 0.9492 |
| **64** | **0.033** | 0.9779 |
| 128 | 0.059 | 0.9887 |
| 256 | 0.104 | 0.9920 |

Cite **0.011 ms** (`efSearch=16`) and **0.033 ms** (`efSearch=64`) as **index-only** ANN times — not client-facing e2e SLOs. HNSW+SQ at `efSearch=64`: **0.011 ms**, R@1 **0.9242**. IVFFlat `nprobe=64`: **0.141 ms**, R@1 **0.9470** ([FAISS Indexing 1M](https://github.com/facebookresearch/faiss/wiki/Indexing-1M-vectors)).

Managed filtered search (vendor research, warm slabs, **excludes client RTT**): YFCC 10M × 192-d recall@10 **0.989 @ ~20 ms**; production customer 35M recall@100 **0.986 @ ~75 ms** ([Pinecone ICML 2025](https://www.pinecone.io/research/ICML_2025.pdf)).

### Latency SLA targets

> ⚠️ **Gap**: No universal vendor **p50 / p95 / p99** contractual SLOs for end-to-end retrieve (embed RTT + filter + ANN + hydrate) are cited in the research. Public docs emphasize architecture and knobs; Faiss and ICML numbers are microbenchmarks / internal latencies. Treat the table below as engineering targets, not guarantees.

**[inferred] end-to-end p50 / p95 / p99 (ms)** — arithmetic for a managed dense retrieve with mandatory metadata filter:

| Stage | p50 ms | p95 ms | p99 ms | Notes |
| --- | --- | --- | --- | --- |
| Embed API RTT | 25 | 80 | 200 | **[inferred]** network+model; not Faiss |
| Filter / bitmap | 2 | 8 | 25 | **[inferred]**; grows with selectivity work |
| ANN (warm) | 5 | 25 | 75 | Index-only Faiss is **0.011–0.033 ms**; managed path + cold slab dominates ([FAISS](https://github.com/facebookresearch/faiss/wiki/Indexing-1M-vectors); [Pinecone arch](https://docs.pinecone.io/guides/get-started/database-architecture)) |
| Hydrate payloads | 3 | 12 | 40 | **[inferred]** ID→payload fetch |
| **E2E sum** | **35** | **125** | **340** | Additive stages; p99 includes cold-slab / strict-filter tails |

\[
\begin{aligned}
T_{\text{e2e}}^{\text{p50}} &\approx 25 + 2 + 5 + 3 = 35\text{ ms} \\
T_{\text{e2e}}^{\text{p95}} &\approx 80 + 8 + 25 + 12 = 125\text{ ms} \\
T_{\text{e2e}}^{\text{p99}} &\approx 200 + 25 + 75 + 40 = 340\text{ ms}
\end{aligned}
\]

| Tier | **[inferred]** target | Mitigations |
| --- | --- | --- |
| **p50** | **≤ 40 ms** retrieve | Keep hot working set in RAM; parallel embed only when text not pre-embedded; modest `efSearch` |
| **p95** | **≤ 150 ms** | Raise `ef` only for recall-critical tenants; payload indexes **before** ingest (Qdrant); avoid unindexed filter fields (strict mode 400) |
| **p99** | **≤ 400 ms** | Warm critical slabs; cap scan tuples / use iterative scans carefully; shed to lower `ef` or lexical under load; bulkhead embed vs ANN |

### Throughput & back-pressure — `efSearch` as the recall/QPS dial

| Lever | Effect |
| --- | --- |
| **↑ `efSearch` / `ef`** | Higher recall@k, higher latency, lower QPS |
| **↓ `efSearch`** | Higher QPS, recall cliff risk (SIFT1M R@1 0.874 @ ef=16 vs 0.978 @ ef=64) ([FAISS Indexing 1M](https://github.com/facebookresearch/faiss/wiki/Indexing-1M-vectors)) |
| **IVF `nprobe`** | Same dial for cell-probe indexes |
| **Back-pressure** | Token-bucket at gateway; when ANN breaker opens → exact re-rank on small candidate set or lexical BM25; reject expensive unindexed filters (Qdrant Cloud strict mode) ([Qdrant hybrid course](https://qdrant.tech/course/beginners/module-3/hybrid-search-in-qdrant/)) |
| **QPS proxy [inferred]** | From published 0.033 ms/query under batch/20-thread Faiss: ~**30k QPS/core-batch** — **do not** treat as production client p99 capacity |

### NFR: availability, RPO/RTO, compliance

| NFR | Posture | Notes |
| --- | --- | --- |
| **Availability** | Multi-replica query path; degrade ANN → exact-on-shortlist → lexical rather than 5xx empty answers | Pinecone rate limits on query path ([Pinecone architecture](https://docs.pinecone.io/guides/get-started/database-architecture)) |
| **RPO** | WAL/request-log durability before ACK → near-zero for accepted writes; index visibility may lag builder/compaction | Pinecone LSN + memtable for read-your-writes ([Pinecone architecture](https://docs.pinecone.io/guides/get-started/database-architecture)); pgvector inherits Postgres WAL/PITR |
| **RTO** | Hot replica / reload index: minutes; full HNSW rebuild: hours (CPU-heavy; newsletter flags rebuild as incident differentiator) ([Newsletter](https://newsletter.systemdesign.one/p/what-is-a-vector-database); [Qdrant Indexing](https://qdrant.tech/documentation/manage-data/indexing/)) | |
| **Compliance** | Encryption at rest; Cloud SOC2/HIPAA are **vendor certifications**, not ANN properties **[inferred]**; right-to-erasure must honor tombstone latency | Embeddings of PII remain PII-bearing **[inferred from architecture]** |

**Explicit trade-off — recall vs latency via `ef`**: Raising `efSearch` from 16 → 64 moves Faiss SIFT1M R@1 **0.874 → 0.978** while index-only time **0.011 → 0.033 ms** (3×); at managed e2e the same dial dominates p95/p99 more than the microbench suggests ([FAISS Indexing 1M](https://github.com/facebookresearch/faiss/wiki/Indexing-1M-vectors)).

**Explicit trade-off — filter-first vs post-filter**: Pre-filter / filter-aware graphs preserve recall under selective predicates (Weaviate allow-list, Qdrant filterable HNSW, Pinecone adaptive bitmaps). Post-filter (pgvector default ANN path) is simpler ops but **under-fills top-k** when selectivity is low — buy iterative scans / partial HNSW or accept wrong neighbors ([pgvector README](https://github.com/pgvector/pgvector)).

---

## Part 4 — Distributed Resilience & Security

### Index build vs query durability

| Path | Durability contract | Failure if violated |
| --- | --- | --- |
| **Write / index build** | Durable log/WAL before ACK; background builder materializes HNSW/IVF into segments/slabs; payload indexes should exist **before** heavy ingest (else HNSW rebuild) ([Pinecone architecture](https://docs.pinecone.io/guides/get-started/database-architecture); [Qdrant Indexing](https://qdrant.tech/documentation/manage-data/indexing/)) | Lost ack → client retry duplicates (need idempotent IDs); building without payload indexes → expensive reindex |
| **Query** | Read merges durable segments + memtable; tombstones hide deletes; no in-place slab rewrite ([Pinecone architecture](https://docs.pinecone.io/guides/get-started/database-architecture); [ICML 2025](https://www.pinecone.io/research/ICML_2025.pdf)) | Serving only stale slabs → missing fresh upserts; ignoring tombstones → resurrected deletes |
| **Faiss library** | Explicit `write_index` / reload; **no** built-in replication ([FAISS wiki](https://github.com/facebookresearch/faiss/wiki)) | Process death without snapshot → empty worker |

pgvector: vector indexes ride Postgres checkpoints/replication; HNSW build needs enough `maintenance_work_mem` or graph spill slows builds ([pgvector README](https://github.com/pgvector/pgvector)).

### Failure taxonomy

| Failure | Class | Symptom | Mitigation |
| --- | --- | --- | --- |
| **Transient** timeout / 429 / cold slab | Transient | p99 spike | Retry + jitter; warm hot namespaces; lower `ef` under load |
| **Stale index** | Consistency | Fresh docs missing from ANN | Read memtable+slabs; wait for builder; blue/green alias after rebuild |
| **Filter leakage / omission** | Security (permanent until fixed) | Cross-tenant chunks | Gateway-mandatory `tenant_id`; never trust client-supplied filter alone ([Qdrant issue #8015](https://github.com/qdrant/qdrant/issues/8015)) |
| **Post-filter recall collapse** | Permanent for that config | Under-filled / wrong top-k | Iterative scans, filterable HNSW, adaptive IVF, raise `ef` |
| **Graph disconnection** (deletes / multi-filter) | Permanent until reindex/ACORN | High latency, low recall | Payload edges + ACORN; rebuild ([Qdrant Indexing](https://qdrant.tech/documentation/manage-data/indexing/)) |
| **Quantization / ANN miss** | Approximate-by-design | True neighbor absent | Raise `ef`/`nprobe`; re-rank shortlist full-precision |
| **Dim/metric mismatch** | Permanent config bug | Garbage neighbors | Pin model+opclass at CONTROL PLANE |
| **Poison upsert** (bad dim / NaN) | Permanent for that ID | Query errors / skew | Validate at proxy; DLQ bad points |

**Idempotency**: client-supplied point IDs on upsert; deletes are tombstones, not silent drops.

### Circuit breaker: closed → open → half-open

Application-layer (store-native breakers are mostly rate limits) **[inferred]**:

```
  successes                         cooldown elapsed
      │                                    │
      ▼                                    ▼
 ┌─────────┐  failures ≥ N    ┌─────────┐ probe OK ┌───────────┐
 │ CLOSED  │ ───────────────► │  OPEN   │ ───────► │ HALF-OPEN │
 └─────────┘                  └─────────┘          └─────┬─────┘
      ▲                         ▲  fail                │
      │                         └──────────────────────┘
      └──────── probe success ─────────────────────────┘
```

- **CLOSED**: ANN path serves traffic; count consecutive/windowed failures.  
- **OPEN**: fail fast to fallback; no ANN calls until cooldown.  
- **HALF-OPEN**: single probe query; success → CLOSED, failure → OPEN.

Pinecone validates against rate/object limits on the query path ([Pinecone architecture](https://docs.pinecone.io/guides/get-started/database-architecture)).

### Fallback chain

**ANN → exact on small candidate set → lexical**

1. Primary: filtered HNSW/IVF ANN at configured `ef`/`nprobe`.  
2. Secondary: if ANN breaker open or top-k under-filled, **exact** distance on a bounded candidate set (allow-list IDs, BM25 shortlist, or IVF list contents) — Faiss Flat / brute force on ≤ few thousand vectors.  
3. Tertiary: **lexical** BM25 / Postgres FTS / sparse-only; return with `degraded=true` in telemetry.  
4. Optional: abstain if compliance forbids unfiltered lexical across tenants.

### Enterprise security & governance

> ⚠️ **Gap**: Vector DB product docs rarely specify NER/PII pipelines or a standard “vector audit schema.” PII redaction and WORM audit below are **ingestion/control-plane** responsibilities; mark thin store-native areas **[inferred]**.

| Control | Requirement |
| --- | --- |
| **Tenant isolation** | **Payload/`tenant_id` filter is not RBAC.** Soft isolation = shared index + mandatory filter every query; hard isolation = namespace/collection/DB per tenant. JWT collection RBAC (Qdrant) scopes *which collection*, not auto-injection of tenant claims ([Qdrant Multitenancy](https://qdrant.tech/documentation/manage-data/multitenancy/); [issue #8015](https://github.com/qdrant/qdrant/issues/8015)). Shared pgvector HNSW lets foreign vectors affect recall — prefer partitioning / partial indexes ([pgvector README](https://github.com/pgvector/pgvector)). |
| **Zero-Trust MCP on `retrieve`** | MCP tool proxy authenticates caller, mTLS to store, injects tenant+ACL filters server-side, denies raw unscoped search tools **[inferred]** enterprise pattern on top of store API keys ([Pinecone architecture](https://docs.pinecone.io/guides/get-started/database-architecture); [Qdrant security](https://qdrant.tech/documentation/tutorials-operations/secure-qdrant/)). |
| **Tool RBAC** | Least privilege: `retrieve` vs `upsert` vs `admin` (create index / delete collection). Qdrant JWT `access`: `r` / `m` / per-collection `r`/`rw` ([Qdrant security docs](https://github.com/qdrant/landing_page/blob/master/qdrant-landing/content/documentation/security.md)). |
| **PII before embed** | Detect → redact/tokenize → then embed. Vectors of raw PII remain regulated artifacts; encrypt at rest; deletes must reach tombstones **[inferred]**. |
| **Immutable ingest audit** | Append-only log: source doc id, chunk id, model/dim, tenant, redaction decision, actor, timestamp — Cloud API logs / `pgaudit` / app WORM lane **[inferred]**; not an ANN feature. |

---

## Part 5 — Production Enterprise Code

Runnable **in-memory filtered ANN** (labeled **brute-force exact** over the filtered set — correct ANN semantics for teaching resilience, not HNSW). Retries + jitter, circuit breaker (`closed → open → half-open`), fallback **ANN → exact shortlist → lexical**, correlation IDs. Deterministic. No API keys. No TODOs.

```python
#!/usr/bin/env python3
"""Filtered in-memory vector retrieve with enterprise resilience primitives."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import random
import time
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
            "path": getattr(record, "path", None),
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


LOG = build_logger("vector.retrieve")


# ---------------------------------------------------------------------------
# Failure taxonomy + retries with exponential backoff and jitter
# ---------------------------------------------------------------------------

class FailureKind(str, Enum):
    TRANSIENT = "transient"
    PERMANENT = "permanent"


class VectorError(Exception):
    def __init__(self, message: str, kind: FailureKind) -> None:
        super().__init__(message)
        self.kind = kind


def retry_with_jitter(
    fn: Callable[[], Any],
    *,
    correlation_id: str,
    stage: str,
    rng: random.Random,
    max_attempts: int = 4,
    base_delay_s: float = 0.001,
    max_delay_s: float = 0.008,
) -> Any:
    last: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except VectorError as exc:
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
            delay = delay * (0.5 + rng.random())  # full jitter in [0.5, 1.5)×
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
    cooldown_s: float = 0.02
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
# Deterministic embed + brute-force filtered ANN (labeled exact over filter)
# ---------------------------------------------------------------------------

def fake_embed(text: str, dim: int = 16) -> list[float]:
    """Deterministic bag-of-tokens embedding in R^dim. No network, no keys."""
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
class Point:
    point_id: str
    tenant_id: str
    text: str
    tags: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class Hit:
    point_id: str
    score: float
    text: str
    path: str


class FilteredBruteForceIndex:
    """
    In-memory index. Search is BRUTE-FORCE EXACT over points that pass the
    metadata filter (filter-first, then full scan). Not HNSW — labeled for clarity.
    """

    def __init__(self, points: list[Point], dim: int = 16) -> None:
        self.dim = dim
        self.points = list(points)
        self.vectors = {p.point_id: fake_embed(p.text, dim=dim) for p in points}
        self._fail_ann_remaining = 0

    def inject_transient_ann_failures(self, n: int) -> None:
        self._fail_ann_remaining = n

    def _filter(self, tenant_id: str, required_tag: str | None) -> list[Point]:
        out: list[Point] = []
        for p in self.points:
            if p.tenant_id != tenant_id:
                continue
            if required_tag is not None and required_tag not in p.tags:
                continue
            out.append(p)
        return out

    def ann_search(
        self,
        query_vec: Sequence[float],
        tenant_id: str,
        required_tag: str | None,
        top_k: int,
    ) -> list[Hit]:
        # Simulated transient ANN dependency failure (e.g. shard timeout).
        if self._fail_ann_remaining > 0:
            self._fail_ann_remaining -= 1
            raise VectorError("ann_shard_timeout", FailureKind.TRANSIENT)

        candidates = self._filter(tenant_id, required_tag)
        scored = [
            (cosine(query_vec, self.vectors[p.point_id]), p) for p in candidates
        ]
        scored.sort(key=lambda t: t[0], reverse=True)
        return [
            Hit(point_id=p.point_id, score=s, text=p.text, path="ann_bruteforce")
            for s, p in scored[:top_k]
        ]

    def exact_on_shortlist(
        self,
        query_vec: Sequence[float],
        shortlist_ids: Sequence[str],
        tenant_id: str,
        top_k: int,
    ) -> list[Hit]:
        by_id = {p.point_id: p for p in self.points}
        scored: list[tuple[float, Point]] = []
        for pid in shortlist_ids:
            p = by_id.get(pid)
            if p is None or p.tenant_id != tenant_id:
                continue
            scored.append((cosine(query_vec, self.vectors[pid]), p))
        scored.sort(key=lambda t: t[0], reverse=True)
        return [
            Hit(point_id=p.point_id, score=s, text=p.text, path="exact_shortlist")
            for s, p in scored[:top_k]
        ]

    def lexical(
        self,
        query: str,
        tenant_id: str,
        required_tag: str | None,
        top_k: int,
    ) -> list[Hit]:
        q = set(query.lower().split())
        scored: list[tuple[float, Point]] = []
        for p in self._filter(tenant_id, required_tag):
            terms = set(p.text.lower().split())
            overlap = len(q & terms)
            if overlap:
                scored.append((float(overlap), p))
        scored.sort(key=lambda t: t[0], reverse=True)
        return [
            Hit(point_id=p.point_id, score=s, text=p.text, path="lexical")
            for s, p in scored[:top_k]
        ]


# ---------------------------------------------------------------------------
# Retrieve orchestration: embed → filter → ANN → hydrate (+ fallback)
# ---------------------------------------------------------------------------

@dataclass
class RetrieveResult:
    hits: list[Hit]
    correlation_id: str
    path: str
    breaker: str
    degraded: bool


class VectorRetrieveService:
    def __init__(
        self,
        index: FilteredBruteForceIndex,
        *,
        rng_seed: int = 7,
        shortlist_ids: Sequence[str] | None = None,
    ) -> None:
        self.index = index
        self.rng = random.Random(rng_seed)
        self.breaker = CircuitBreaker(name="ann")
        # Precomputed lexical/BM25-ish candidate IDs for exact fallback.
        self.shortlist_ids = list(shortlist_ids or [])

    def retrieve(
        self,
        query: str,
        *,
        tenant_id: str,
        required_tag: str | None = None,
        top_k: int = 3,
        correlation_id: str | None = None,
    ) -> RetrieveResult:
        cid = correlation_id or f"corr-{hashlib.sha256(query.encode()).hexdigest()[:12]}"
        LOG.info(
            "retrieve_start",
            extra={"correlation_id": cid, "tenant_id": tenant_id, "stage": "start"},
        )

        # 1) Embed (deterministic local; production would call embedding API)
        query_vec = fake_embed(query, dim=self.index.dim)

        # 2–4) filter → ANN → hydrate, with breaker + retries
        if self.breaker.allow():
            try:
                hits = retry_with_jitter(
                    lambda: self.index.ann_search(
                        query_vec, tenant_id, required_tag, top_k
                    ),
                    correlation_id=cid,
                    stage="ann",
                    rng=self.rng,
                )
                self.breaker.record_success()
                LOG.info(
                    "retrieve_ok",
                    extra={
                        "correlation_id": cid,
                        "tenant_id": tenant_id,
                        "path": "ann_bruteforce",
                        "breaker": self.breaker.state.value,
                    },
                )
                return RetrieveResult(
                    hits=hits,
                    correlation_id=cid,
                    path="ann_bruteforce",
                    breaker=self.breaker.state.value,
                    degraded=False,
                )
            except VectorError:
                self.breaker.record_failure()
                LOG.warning(
                    "ann_failed_fallback",
                    extra={
                        "correlation_id": cid,
                        "tenant_id": tenant_id,
                        "breaker": self.breaker.state.value,
                        "fallback": "exact_shortlist",
                    },
                )
        else:
            LOG.warning(
                "breaker_open_skip_ann",
                extra={
                    "correlation_id": cid,
                    "tenant_id": tenant_id,
                    "breaker": self.breaker.state.value,
                    "fallback": "exact_shortlist",
                },
            )

        # Fallback 1: exact on small candidate set (still tenant-scoped)
        if self.shortlist_ids:
            hits = self.index.exact_on_shortlist(
                query_vec, self.shortlist_ids, tenant_id, top_k
            )
            if hits:
                return RetrieveResult(
                    hits=hits,
                    correlation_id=cid,
                    path="exact_shortlist",
                    breaker=self.breaker.state.value,
                    degraded=True,
                )

        # Fallback 2: lexical
        hits = self.index.lexical(query, tenant_id, required_tag, top_k)
        LOG.info(
            "retrieve_degraded_lexical",
            extra={
                "correlation_id": cid,
                "tenant_id": tenant_id,
                "path": "lexical",
                "breaker": self.breaker.state.value,
                "fallback": "lexical",
            },
        )
        return RetrieveResult(
            hits=hits,
            correlation_id=cid,
            path="lexical",
            breaker=self.breaker.state.value,
            degraded=True,
        )


def _demo_corpus() -> list[Point]:
    return [
        Point("a1", "tenantA", "refund policy for annual subscriptions", frozenset({"billing"})),
        Point("a2", "tenantA", "how to reset password and mfa tokens", frozenset({"auth"})),
        Point("a3", "tenantA", "vector index hnsw recall and ef search tuning", frozenset({"eng"})),
        Point("b1", "tenantB", "refund policy secret cross tenant leak bait", frozenset({"billing"})),
        Point("b2", "tenantB", "password reset runbook for tenant b only", frozenset({"auth"})),
    ]


def main() -> None:
    # Deterministic RNG seed for jitter; fixed corpus.
    corpus = _demo_corpus()
    index = FilteredBruteForceIndex(corpus, dim=16)
    shortlist = ["a1", "a2", "a3"]  # tenantA eng/billing/auth IDs only
    svc = VectorRetrieveService(index, rng_seed=42, shortlist_ids=shortlist)

    # Happy path: filter-first brute ANN, tenant A + billing tag
    r1 = svc.retrieve(
        "annual subscription refund",
        tenant_id="tenantA",
        required_tag="billing",
        top_k=2,
        correlation_id="corr-demo-001",
    )
    assert r1.path == "ann_bruteforce"
    assert all(h.point_id.startswith("a") for h in r1.hits)
    assert r1.hits[0].point_id == "a1"

    # Trip breaker: each retrieve counts as one failure after retries exhaust.
    # failure_threshold=3 → three failed retrieves open the breaker.
    index.inject_transient_ann_failures(20)
    degraded_paths = []
    for i in range(3):
        rx = svc.retrieve(
            "hnsw ef search recall",
            tenant_id="tenantA",
            required_tag="eng",
            top_k=1,
            correlation_id=f"corr-demo-00{i+2}",
        )
        assert rx.degraded is True
        assert rx.path in {"exact_shortlist", "lexical"}
        assert all(h.point_id.startswith("a") for h in rx.hits)
        degraded_paths.append(rx.path)
    r2 = rx
    assert svc.breaker.state.value == "open"

    # OPEN → cooldown → HALF_OPEN probe; ANN still failing → OPEN → shortlist/lexical
    time.sleep(svc.breaker.cooldown_s + 0.001)
    assert svc.breaker.allow()  # transitions OPEN → HALF_OPEN
    assert svc.breaker.state.value == "half_open"
    r3 = svc.retrieve(
        "reset password mfa",
        tenant_id="tenantA",
        required_tag="auth",
        top_k=1,
        correlation_id="corr-demo-005",
    )
    assert r3.degraded is True
    assert all(h.point_id.startswith("a") for h in r3.hits)
    assert r3.path in {"exact_shortlist", "lexical"}
    assert svc.breaker.state.value == "open"

    print(
        json.dumps(
            {
                "r1": {"path": r1.path, "ids": [h.point_id for h in r1.hits], "cid": r1.correlation_id},
                "r2": {"path": r2.path, "ids": [h.point_id for h in r2.hits], "breaker": r2.breaker},
                "r3": {"path": r3.path, "ids": [h.point_id for h in r3.hits], "breaker": r3.breaker},
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
```

Run: `python3 modules/11-how-vector-databases-work.md` is not valid — extract the code block or save as `filtered_ann_retrieve.py`. From a copied file: `python3 filtered_ann_retrieve.py` (stdlib only).

---

## Part 6 — Architectural System Design Scenarios

Exactly **two** scenarios. Interview prompts only after both.

### Scenario 1 — Postgres-native product search (<100k vectors)

**Problem**: B2B SaaS catalog search over **~80k** product embeddings (1536-d), moderate QPS (~50), must **join** ANN results to Postgres inventory/price tables in one transaction boundary, selective filters on `org_id` + category, target **[inferred]** retrieve p95 ≤ 150 ms, team already runs Postgres HA.

**Proposed architecture** (component diagram — not Part 1):

```
  ┌────────────┐     ┌──────────────────┐     ┌────────────────────────────┐
  │ App / MCP  ├──►  │ Query Gateway    ├──►  │ Postgres + pgvector        │
  │ retrieve   │     │ force org_id     │     │  ┌────────┐  ┌──────────┐  │
  └────────────┘     │ filter + deadline│     │  │ heap   │  │ HNSW     │  │
                     └────────┬─────────┘     │  │+B-tree │  │(+partial)│  │
                              │               │  └────┬───┘  └────┬─────┘  │
                              │               │       └──── join ─┘        │
                              │               └─────────────┬──────────────┘
                              ▼                             ▼
                     ┌────────────────┐            ┌────────────────┐
                     │ Embed sidecar  │            │ WAL · PITR ·   │
                     │ (text→vector)  │            │ replica query  │
                     └────────────────┘            └────────────────┘
```

**Technology choices**: `pgvector` HNSW (`m=16`, tune `hnsw.ef_search`), B-tree / partial HNSW on `org_id`, enable iterative scans for selective filters, `halfvec` if RAM-bound; gateway injects `org_id` (never trust client filter alone) ([pgvector README](https://github.com/pgvector/pgvector); [Newsletter](https://newsletter.systemdesign.one/p/what-is-a-vector-database)).

**Trade-off matrix**

| Approach | Cost | Latency | Ops | Security | Scalability |
| --- | --- | --- | --- | --- | --- |
| **A. Exact scan (no ANN)** | Low infra | Poor as \(N\)→100k+ | Lowest | Joins+RLS natural | Hits O(n) wall (~newsletter arithmetic) |
| **B. pgvector HNSW + iterative/partial (recommended)** | Low (shared Postgres) | Good warm; post-filter risk mitigated | Low–med (vacuum, `maintenance_work_mem`) | RLS + mandatory `org_id`; filter ≠ JWT alone | Fits &lt;100k–low millions; rebuild cost grows |
| **C. Sidecar Faiss HNSW** | Med (extra fleet) | Best batch ANN (0.011–0.033 ms index-only) | High (you own sync/replication) | DIY tenant filter; easy to leak | Strong ANN; weak SQL join story |

**Decision rationale**: Choose **B**. Newsletter heuristic places this workload in the pgvector band; transactional joins and PITR outweigh Faiss’s microbench wins. Accept post-filter semantics only with iterative scans / partial indexes; escalate to dedicated filterable HNSW when \(N\) and QPS leave Postgres comfort zone.

---

### Scenario 2 — Multi-tenant filtered ANN at 10M–35M

**Problem**: Vertical SaaS knowledge retrieve API: **10k tenants**, **10M–35M** chunks, every query carries categorical/numeric metadata, need **≥0.98** recall@k under selectivity from tens to millions of matches, no cross-tenant leakage, continuous ingest, p99 tolerates safety over squeezing last milliseconds.

**Proposed architecture** (component diagram — not Part 1):

```
                 ┌─────────────────────────────────────────────┐
                 │         CONTROL: schema · tenancy policy      │
                 │  namespaces / is_tenant · JWT tool RBAC       │
                 └────────────────────┬────────────────────────┘
                                      │
    docs     ┌────────────────────────▼────────────────────────┐
  ┌──────┐   │ Ingest workflow (durable)                       │
  │Object├──►│ PII detect→redact→audit → embed → upsert        │
  │store │   │ payload indexes BEFORE bulk load                │
  └──────┘   └────────────┬───────────────────┬────────────────┘
                          │                   │
                          ▼                   ▼
                 ┌─────────────────┐   ┌──────────────────────┐
                 │ Vector data     │   │ Immutable ingest     │
                 │ plane: bitmap / │   │ audit (WORM)         │
                 │ filterable HNSW │   └──────────────────────┘
                 │ / adaptive IVF  │
                 └────────┬────────┘
                          │
                 ┌────────▼────────┐
                 │ MCP retrieve    │  Zero-Trust: mTLS, inject tenant_id,
                 │ tool proxy      │  deny unscoped search, rate limits
                 └─────────────────┘
```

**Technology choices**: Managed adaptive filtered search (Pinecone serverless evidence: YFCC recall@10 **0.989 @ ~20 ms**; customer recall@100 **0.986 @ ~75 ms**) **or** self-hosted Qdrant filterable HNSW (`m=16`, `ef_construct=100`, `is_tenant=true`, payload indexes first) + hybrid dense+sparse RRF; MCP Zero-Trust retrieve; hard namespaces for regulated tenants ([ICML 2025](https://www.pinecone.io/research/ICML_2025.pdf); [Qdrant Multitenancy](https://qdrant.tech/documentation/manage-data/multitenancy/); [Qdrant Indexing](https://qdrant.tech/documentation/manage-data/indexing/)).

**Trade-off matrix**

| Approach | Cost | Latency | Ops | Security | Scalability |
| --- | --- | --- | --- | --- | --- |
| **A. Shared HNSW + app post-filter only** | Lowest density | Low when warm | Low | **Unsafe** — filter omission = cross-tenant leak | High density; fails audit |
| **B. Filter-aware / adaptive ANN + gateway-mandatory tenant (recommended)** | Med (managed or self-host RAM) | Strong (20–75 ms internal in cited benches) | Med (payload indexes, compaction) | Strong if proxy injects tenant; JWT≠payload | 10M–35M class with selectivity sweeps |
| **C. Namespace / index per tenant** | Highest idle cost | Predictable isolation | High at 10k tenants | Strongest blast-radius | Ops ceiling unless pooled for small tenants |

**Decision rationale**: Default **B** — co-design metadata bitmaps / filterable HNSW with ANN; never assume unfiltered `ef` transfers to filtered traffic ([ICML 2025](https://www.pinecone.io/research/ICML_2025.pdf)). Escalate hot/PHI tenants to **C**. Reject **A**: soft tenancy without enforced filters is a **security** bug, not a tuning issue ([Qdrant issue #8015](https://github.com/qdrant/qdrant/issues/8015)). Pair with PII-before-embed and immutable ingest audit **[inferred]** control-plane controls.

---

### Interview prompts (after both scenarios)

1. Draw control vs data plane for Pinecone serverless and walk **embed → filter → ANN → hydrate** with memtable vs slab reads.
2. Derive `$ / 1k queries` with labeled embed prices and show why 1536-d @ ~6 KB/vector dominates over embed API cents at \(N=10^7\).
3. Given only Faiss **0.011 ms / 0.033 ms** index-only points, build **[inferred]** e2e p50/p95/p99 including embed RTT; list mitigations per tier.
4. Why is `efSearch` both a recall dial and a QPS back-pressure lever? Quote SIFT1M R@1 vs ms.
5. Contrast pgvector **post-filter** vs Weaviate **pre-filter** vs Qdrant **filterable HNSW** on a 1% selectivity predicate.
6. Design the breaker chain **ANN → exact shortlist → lexical** with closed/open/half-open and when you abstain.
7. Explain why payload `tenant_id` filter is not RBAC; what does Qdrant JWT actually scope?
8. When do you leave pgvector for a dedicated engine? Use the ~100k heuristic and the 10M–35M filtered-recall evidence.

---

## Sources (selected)

- [System Design Newsletter — What is a Vector Database](https://newsletter.systemdesign.one/p/what-is-a-vector-database) — O(n) arithmetic, ~6 KB/vector, pgvector heuristic  
- [HNSW paper (arXiv:1603.09320)](https://arxiv.org/abs/1603.09320) — graph ANN, `M`/`ef`, complexity  
- [FAISS indexes](https://github.com/facebookresearch/faiss/wiki/Faiss-indexes) · [Indexing 1M](https://github.com/facebookresearch/faiss/wiki/Indexing-1M-vectors) — bytes/vector, SIFT1M ms/R@1  
- [pgvector README](https://github.com/pgvector/pgvector) — HNSW/IVFFlat, post-filter, iterative scans  
- [Pinecone architecture](https://docs.pinecone.io/guides/get-started/database-architecture) · [ICML 2025](https://www.pinecone.io/research/ICML_2025.pdf) — slabs, filtered recall/latency  
- [Qdrant Indexing](https://qdrant.tech/documentation/manage-data/indexing/) · [Multitenancy](https://qdrant.tech/documentation/manage-data/multitenancy/) · [security](https://qdrant.tech/documentation/tutorials-operations/secure-qdrant/)  
- [Weaviate vector index](https://docs.weaviate.io/weaviate/config-refs/indexing/vector-index) · [filtering](https://archive.docs.weaviate.io/weaviate/concepts/filtering)  
- Full bibliography: `research/11-how-vector-databases-work.md`
