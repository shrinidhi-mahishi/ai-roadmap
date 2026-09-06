# Module 18: AI System Design

**Study + interview prep.** Grounded in research dated 2026-09-03 (57 sources). This file is the **interview system-design round**: how to **compose** modules [01-rag.md](01-rag.md)–[17-llmops-cicd.md](17-llmops-cicd.md) into one product under **45–60 minutes**. It is **not** RAG chunking / Adaptive-RAG internals (01), **not** HNSW / Ananas / RU math (15), **not** DSPy GEPA / few-shot compilers (16), **not** Agent Server SSE / PostgresSaver internals (13), **not** the MCP spec. Cite those files. Four whiteboard archetypes: **chatbot**, **search**, **copilot**, **moderation**. Guardrails ([07-guardrails.md](07-guardrails.md)) and **Zero-Trust MCP** (07 / [09-deep-agents-execution.md](09-deep-agents-execution.md) / 15 / 17) are **default boxes on every board**, not polish in the last two minutes. **The model is never the PDP.** `$ per 1k` is **[inferred]** from GPT-5.6 **Luna $0.20 / $1.20**, **Terra $2 / $12**, Claude **Sonnet 4.6 $3 / $15**, cache **1.25× write / 0.1× read** ([03-caching.md](03-caching.md)) × a **stated shape**, not a vendor SKU. Product p50/p95/p99 are **[inferred] architecture-derived policy** unless a named engineer quote exists (Cheney Copilot **mean < 200 ms** is **mean**, not p99). Do **not** invent a GitHub Copilot “p99 80 ms SLO.”

> ⚠️ Gap: GPT-5.6 Luna/Terra/Sol list rates are from the **search index** of [openai.com/api/pricing](https://openai.com/api/pricing/) — HTML was **JS-gated** on 2026-09-03. Do not treat index rates as a scraped HTML table.

---

## What Is This?

**AI product system design is not “LLM + vector DB.”** It is a **composition round**: same five planes (control / data / persistence / tool proxies / telemetry), four **flight plans**. You drive 45–60 minutes. Interviewers score requirement questions that **change boxes**, where identity/ACL lives, what you **drop**, a capacity envelope, one or two deep dives, failure/scale, wrap.

This round is **none** of the following (ask which clock they want in the first 60 seconds):

| Interview they might mean | What they actually score | If you draw this instead |
| --- | --- | --- |
| **This file — product AI** | Chat, search, copilot, or moderation over **pretrained** models. Orchestration, ACL, rails, spend, eval **off** p99 | Correct board |
| **Classic HLD (“design Twitter”)** | Feeds, fan-out, caches, shard keys, 10×/100× | You never name a PDP or a hop cap |
| **Google/Meta ML system design** | Feature store, training loop, batch/online skew, drift ([Google](https://www.systemdesignhandbook.com/guides/google-ml-system-design-interview/) / [Meta](https://www.systemdesignhandbook.com/guides/meta-ml-system-design-interview/) guides) | You invent a YouTube-recs trainer for a Drive Q&A bot |
| **LLM serving** | PagedAttention, prefill/decode, continuous batching, goodput | You quote tok/s as the product SLO |

Think of an **airport**, not a single aircraft. **Control** is ATC: IdP → PEP, PAP/PDP (Cedar/OPA), feature-flag **bundle** `(model_id, prompt_hash, schema_hash, decoding_params, index_gen, eval_suite_id)` (17), hop caps, spend ledger, HITL queue, MCP allowlist + hash-pin. **Data** is the flight: client → gateway → orchestrator → model / tools / index, input rails → generate/stream → output rails + DLP. **Persistence** is the hangar log: checkpointer, store, index alias, WORM decision log — not the aircraft. **Tool proxies** are MCP **behind a gateway PEP** — identity from the **verified JWT**, never from model JSON. **Telemetry** is traces (ACL-redacted), `$` / tokens, queue depth, decision WORM. If you couple planes, p99 tracks the **wrong clock** (judge, reindex, or human review).

**Interview one-liner:** I compose 01–17. I draw guardrails and Zero-Trust MCP in the HLD, not the wrap. The model is never the PDP. HITL is a **pause** (12), not policy.

## Why It Matters

Principal AI Architect loops for **product** roles ask you to assemble a user-facing system, not to re-derive HNSW or PagedAttention. Interviews test whether you **ask NFR questions that fork architecture** (corpus, latency class, tenancy, regulated, online vs batch, HITL, tools), refuse ANN under **~200k tokens** (01 / Anthropic), put ACL in an **index predicate** not the prompt (01/15; Microsoft semantic-index **permission trimming**), split **completions vs agent** on a copilot board (Cheney **mean < 200 ms**, peak **~8k RPS** — not an agent SLO), and cascade moderation **hash → keyword → classifier → LLM queue → human** (Discord AutoMod **>45M** blocked before post; Perspective **sunset 2026-12-31**).

The cost trap is the **wrong archetype’s meter**: SC N=40 as a chat default (**~$780 / 1k** Sonnet **calls**, 16 — never), fat namespace **~$263k/mo vs ~$8.3k/mo** (15), copilot tool-loop **$100.80 → $252 / 1k** Terra 12→30 loops **[inferred]**, LLM-every UGC Sonnet **$4.65 / 1k** vs cascade Luna **$0.0085 / 1k** **[inferred]**. The latency trap is quoting Fast mode **99.9%** or Bedrock Reserved **99.5%** as **product** p99 — they are **throughput** and **capacity**. The security trap is “add safety at the end,” host shell, or treating the model as the PDP.

---

### 1. System Topology & Data Flow

Five planes, **not** a blob of intelligence. Draw the **generic product** first. Archetype deltas are §2 — they add/drop boxes; they do not replace this diagram.

```
                         TELEMETRY / OBSERVABILITY SINKS  (05)
         ┌──────────────────────────────────────────────────────────────────┐
         │  traces (ACL-redacted spans)   $ / tokens   queue depth          │
         │  nDCG@k / task metric SIDECAR — judge OFF user p99 (04/17)       │
         │  WORM: (ts, actor_id, action, policy_hash, scores, prev_hash)    │
         │  flag assignment: salt, pct, thread_id stickiness (17)           │
         └────────────▲─────────────────────▲──────────────────▲────────────┘
                      │ spans               │ meters            │ audit
                      │                     │                   │
┌─────────────────────┴─────────────────────┴───────────────────┴───────────┐
│ CONTROL PLANE  (LLM-free at admit — policy, identity, blast radius)       │
│                                                                           │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌─────────┐ │
│  │ IdP → PEP  │ │ PAP / PDP  │ │ bundle     │ │ hop cap /  │ │ HITL    │ │
│  │ JWT        │ │ Cedar/OPA  │ │ alias (17) │ │ spend      │ │ queue   │ │
│  │ tenant+role│ │ side effect│ │ sticky     │ │ ledger     │ │ pause   │ │
│  └─────┬──────┘ └─────┬──────┘ └─────┬──────┘ └─────┬──────┘ └────┬────┘ │
│        │              │              │              │              │      │
│        ▼              ▼              ▼              ▼              ▼      │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │ resolve principal + bundle + policy_hash. Flag SDK local.          │  │
│  │ MCP allowlist + toolSurfaceHash. Judge is NOT on this path.        │  │
│  │ Pin: model_id, prompt_hash, schema_hash, decoding_params,          │  │
│  │      index_gen, eval_suite_id. Tag production is a POINTER.        │  │
│  └────────────────────────────────────────────────────────────────────┘  │
└────────────────────────────────┬──────────────────────────────────────────┘
                                 │ resolved principal + bundle + policy hash
                                 ▼
┌───────────────────────────────────────────────────────────────────────────┐
│ DATA PLANE  (untrusted token stream — model proposes, PEP disposes)       │
│                                                                           │
│  client → gateway → orchestrator (08/13) → model / tools / index          │
│           input rails (07) → generate/stream → output rails + DLP         │
│                                                                           │
│  ┌────────────── TOOL PROXIES (Zero-Trust MCP — identity from JWT) ─────┐ │
│  │ MCP behind gateway PEP: retrieve_* | query_index | execute |         │ │
│  │                         create_ticket | record_decision              │ │
│  │   ns / k / include_values / dest from verified token — NOT JSON      │ │
│  │   deny include_values for assistants (15); deny host bash (09/13)    │ │
│  │   OAuth 2.1 + PKCE, RFC 8707 audience, RFC 8693 NO passthrough       │ │
│  │   hash-pin tool JSON + server digest. MCP is not the PDP             │ │
│  └──────────────────────────────────────────────────────────────────────┘ │
└──────┬───────────────────────────────┬──────────────────┬─────────────────┘
       │                               │                  │
       ▼                               ▼                  ▼
┌──────────────────────────┐ ┌───────────────────┐ ┌────────────────────────┐
│ PERSISTENCE              │ │ SANDBOX / EGRESS  │ │ HUMAN / APPEAL         │
│ checkpointer (13)        │ │ (07/09)           │ │ HITL interrupt (12)    │
│ Store (optional memory)  │ │ Firecracker/      │ │ reviewer RBAC ≠ user   │
│ index alias + ACL bitmap │ │ gVisor/WASM/MXC   │ │ DSA Art. 20 queue      │
│ WORM decision / appeal   │ │ dest allowlist    │ │ dual-control CSAM view │
│ ingest watermark         │ │ creds OUTSIDE     │ │ wellbeing / retention  │
└──────────────────────────┘ └───────────────────┘ └────────────────────────┘
```

**Planes (do not couple):**

| Plane | Lives here | LLM-free? | Failure if coupled |
| --- | --- | --- | --- |
| **Control** | IdP, PDP, bundle alias, hop cap, spend, HITL queue, MCP allowlist | Yes at admit. Judges/dreaming **offline** | Playground string in prod; model picks `tenant_id` |
| **Data** | Rails, retrieve/tools/generate, stream | No — untrusted stream | Judge on user p99; reindex on query replica |
| **Persistence** | Checkpointer, index alias, WORM | Yes | InMemorySaver; mutable moderation “status” |
| **Tool proxies** | MCP behind gateway; identity from JWT | Yes for authz | Omnibus `search(collection)`; host shell |
| **Telemetry** | Spans, `$`, WORM, sidecar eval | Yes | Logging PII/CSAM “for debug” |

**Default boxes on every board (draw in HLD, minutes 10–22 — not the wrap):**

- Input + output rails + DLP (07).
- PDP for side effects (07). HITL is a **pause**, not the PDP (12).
- Zero-Trust MCP gateway if **any** tool exists — including `query_index` / `retrieve_kb` (15, 01).
- Spend cap / TPM bulkhead (07, 17).
- Eval sidecar, not on user p99 (04, 17).

**Request-flow narrative (30-second walk — say this while you draw):**

1. **Admit.** Client presents IdP token. Gateway authenticates; **does not** trust body `user_id`.
2. **Control.** Resolve feature-flag bundle (17). Sticky `thread_id` so a canary does not flip mid-conversation. Judge **off** this path (04).
3. **Input rails (07).** Prompt-injection sensors **cut likelihood**; they do not authorize tools.
4. **Orchestrator (08/13).** One graph. Retrieve / tools / generate as the **archetype** requires — this is the only step that forks.
5. **Tool / MCP.** Gateway PEP, hash-pin `toolSurfaceHash`, RFC 8707 audience, **no** token passthrough (07/09). Namespace from JWT.
6. **Stream** tokens (SSE). Persist checkpoint if the product has a thread (13: disconnect does **not** cancel).
7. **Telemetry.** Spans without secrets; WORM of allow|deny|HITL.

**Interview traps in this diagram:**

- Jumping to “LLM + vector DB” in minute 2 is the dominant fail. HLD Handbook attributes ~**50%** of failed **general** system-design interviews to drawing before clarifying; the AI variant is the same failure with extra tokens.
- Serving is **not** PagedAttention on this board unless they asked for an inference-infra role ([systemdesign.academy Design ChatGPT](https://www.systemdesign.academy/interview/design-chatgpt) is the other clock).
- `sdeoffer` / field-guide split: **stateless app tier** vs **GPU inference tier**. You own the app tier; you **size** the GPU tier.

#### 1.1 45-minute interview motion **[inferred] interview policy**, not a company rubric

Merge of SPIDER (S 5 / P 3 / I 10 / D 15 / E 5 / R 5), Johnny Mai’s Google DeepMind AIE chatbot writeup, and the AI-engineering field guide. Sixty-minute loops add a second deep dive or “now add EU users / now add tools.”

| Min | Phase | Exit criterion |
| --- | --- | --- |
| **0–6** | Clarify + NFR questions that **fork architecture** | Corpus size, latency class, tenancy, regulated data, online vs batch, HITL, tools |
| **6–10** | Capacity envelope | QPS, concurrent sessions, ingest embeddings, judge **off** user p99 (04/17) |
| **10–22** | High-level boxes | Generic topology + archetype deltas; **guardrails + ZT MCP drawn now** |
| **22–38** | Deep dive **1–2** components | The fork (ACL bitmap, sandbox, cascade, checkpointer) — **not** all of 01–17 |
| **38–50** | Failure / 10× scale / wrap | Wrong-archetype trap, cost cliff, dual-running bundles (17) |

SE Radio 704 (Jan 2026): leave **15–20 min** for scale “10× / 100×.” Stated vs unstated requirements; interviewer mistakes exist too. PracHub OpenAI ML system design 2026: safety/privacy **proactively**; caching, routing, batching.

#### 1.2 NFR questions that **fork** the architecture

Ask **5–8**. Each answer **drops or adds a box**. Do not ask “who are the users?” without a fork.

| Question | If they say… | Architecture fork | Cite |
| --- | --- | --- | --- |
| **Corpus size** | < ~**200k tokens** (~500 pages), static | **Drop ANN / RAG.** Stuff + prompt-cache | Anthropic Contextual Retrieval; 01 |
| | Multi-million chunks, multi-tenant | Hybrid retrieve + ACL **predicate** | 01, 15 |
| **Latency class** | Ghost-text / IntelliSense competitor | Completions: cancel, HTTP/2, colocate proxy+model. **Not** 8-turn RAG | Cheney InfoQ |
| | Support chat “a few seconds” | Streaming TTFT + retrieve timeout **200–500 ms policy** | 01 |
| | Batch / overnight digest | Flex/Batch tier; judge may run on the **job** clock | 17 |
| **Tenancy** | Single enterprise | One index alias; still **per-user ACL** | 01 |
| | B2B SaaS, N tenants | Namespace-per-tenant (15 RU cliff) **or** RLS; never “filter in the prompt” | 15 |
| **Regulated data** | PHI / PCI / secrets in corpus | PII **detect → redact → audit before embed**; residency; WORM | 07, 15, 17 |
| | Public web | Citations + freshness; still output rails | 07 |
| **Online vs batch** | Every keystroke / every message | Sync data plane; ingest **async** | 01 ingest vs query |
| | Nightly reindex / dreaming | Control-plane job; do not block p99 | OpenAI Dreaming |
| **HITL** | Refund / shell / promote / takedown | PDP **then** pause. Model is not policy | 07, 12 |
| | Read-only FAQ | Maybe no interrupt; still egress allowlist | 07 |
| **Tools** | None | Chat/search | — |
| | Shell / MCP / tickets | Sandbox (09) + ZT MCP gateway. **Never host shell** | 09, 13 |
| **Modality** | Text | Classifier APIs | OpenAI moderation |
| | Image/video UGC | Hash-match **first** for known CSAM; omni-moderation images **20 MB**; no audio on omni | OpenAI; Google CSAI Match |

---

### 2. Core Mechanics & Algorithms

#### 2.1 What this round scores (vs Google/Meta ML SD)

**I1.** The product is **orchestration around pretrained models**, not a training loop. If the prompt is “design YouTube recommendations,” that is **not** this module. If the prompt is “design an internal Q&A bot over Drive,” it is.

**I2.** Control vs data: identity, bundle, PDP, and hop cap are **LLM-free at admit**. The model proposes tokens and tool JSON; the PEP disposes.

**I3.** Pull vs drop is the skill. Skip ANN if corpus **< ~200k tokens**. Skip host shell always. Skip LLM-every-item on UGC. Skip SC N=40 as a chat default (16). Skip GraphRAG unless they asked “themes” (01).

**I4.** HITL ≠ PDP (12). Deep Agents batches proposed calls and **waits**; it does **not** evaluate `(principal, action, resource)`. Meta **Agents Rule of Two** (31 Oct 2025): a session may have at most two of [A untrusted input, B sensitive data, C state-change/egress]; if all three, **no autonomy** — HITL or split sessions. Same shape as Willison lethal trifecta (07).

**I5.** Eval is a **sidecar** (04/17). Online m3 on the request blows 16 extract **p99 12,000 ms**. Miller **n ≈ 969** is the **promote N**, not a 50-row vibe.

**I6.** A release is the 17 six-tuple. Sticky `thread_id` during canary. Changing `index_gen` without **dual-running indexes** is a retrieval incident.

Published 2025–2026 **product-AI** writeups (not GPU serving): SPIDER; HLD Handbook (quantified NFRs — “p99 500 ms” not “low latency”); Johnny Mai DeepMind AIE chatbot (control vs blob; recovery when retriever fails); field guide §04; sdeoffer Design ChatGPT (stateless app vs GPU inference). Use ChatGPT **900M WAU** (Feb 2026) and **2.5B prompts/day** (~**29k prompts/s** average) only as a **capacity conversion** anecdote — not your B2B SLO.

#### 2.2 Archetype A — Chatbot / assistant (support, internal Q&A)

| Plane | Owns | **Pull** | **Drop** |
| --- | --- | --- | --- |
| Control | Thread ACL, model routing, hop cap, memory-write policy | 07 rails, 16 prompt hash, 17 flags, 13 `thread_id` | Training loop; GraphRAG unless “themes” (01) |
| Data | Turn history in window; optional retrieve; **stream** | 08/13 graph; 03 prefix cache; 01 Adaptive-RAG (skip retrieve on chitchat) | Uncapped ReAct; SC N=40 (16) |
| Persistence | Checkpointer (conversation); **optional** Store (cross-thread) | 13 PostgresSaver | Treating ChatGPT “Dreaming” as a hot-path write |
| Tools | `retrieve_*` scoped by ACL; ticket/refund = HITL | 01 retrieve tools; 12 `interrupt_on` named tools | Omnibus `search(collection)`; host shell |

**Stream.** Same `$` as full-wait (you pay output tokens either way). Streaming buys **TTFT** and cancel. Full-wait is for batch / moderation LLM slow-path only.

**Adaptive-RAG (cite 01, do not recopy):** router: chitchat → no retrieve; exact ID (`TS-999`) → lexical; paraphrase FAQ → hybrid. Retrieve timeout **200–500 ms policy** (01). Hop cap **1–2** for L1 chat.

**Memory is a batch job.** OpenAI: saved memories **April 2024**; first “dreaming” (background curation) **April 2025**; 2026 rewrite synthesizes a profile offline, ~**5×** cheaper so Free can ship. One-liner: **the model is stateless; memory is retrieve-or-inject scaffolding**. Do not write memory on every turn on the user clock.

#### 2.3 Archetype B — Search (enterprise retrieve + answer)

The **product is hybrid retrieval + ACL bitmap**, not a vector library (01/15). Microsoft analog: semantic index + Graph **permission trimming** — results only if the signed-in user already has RBAC access; Retrieval API is **delegated**, not app-only. Web analog (citations, not enterprise ACL): Google AI Overviews / AI Mode **may** use **query fan-out**. Bing Copilot-class writeups describe hybrid BM25 + dense then passage rerank (iPullRank — **secondary**; do not quote as Microsoft SLO).

| Plane | Owns | **Pull** | **Drop** |
| --- | --- | --- | --- |
| Control | `index_gen` alias, ingest watermarks, ACL stamp at **write** | 01 ingest control; 15 schema pin `model_id+dim+metric+index+codec` | Serving `latest` index; embedding swap without dual-run (17/15) |
| Data | Authz filter **before** ANN → hybrid → fuse → rerank → generate → cite | 01 query path; 15 ANN | ANN if corpus **< ~200k tokens**; sequential BM25-then-dense (run **in parallel**) |
| Persistence | Dense + sparse + **ACL bitmap** + caches | 15 | Putting ACL in the prompt after retrieve |
| Tools | `retrieve_public` / `retrieve_hr` separately; identity from JWT | 01 tool proxies | One MCP `search` over all collections |

**Skip-ANN rule (say it out loud):** Anthropic: knowledge base **< 200,000 tokens (~500 pages)** → include the whole KB in the prompt; prompt cache **>2×** latency cut, **up to 90%** cost cut. Past that, contextual embeddings + BM25 (−**49%** top-20 miss) + rerank (−**67%**). **Also drop ANN** if they only need exact SKU/`TS-999` **and** the corpus is small enough for lexical — BM25-only is a valid v1 (01). At high QPS, stuffed Sonnet can **lose on $** vs Terra+RAG even under 200k (§3).

#### 2.4 Archetype C — Copilot (IDE / workspace agent)

**Two products on one board — name which:**

1. **Completions (ghost text):** latency war with local IntelliSense. GitHub: authenticating **copilot-proxy**, short-lived signed tokens (not API keys in the extension), HTTP/2 multiplex + **cancel typed-through** (~**half** of issued requests), GLB/HAProxy end-to-end H2 (cloud LBs terminated H2), **colocate proxy with Azure model**. **>400M completions/day** at talk pitch; peak **~8,000 RPS**; **mean** response **< 200 ms** (Cheney, InfoQ). That is **mean**, not p99, not an SLO. Do **not** invent p99 80 ms.
2. **Agentic session:** tools, sandbox, HITL. GitHub **2026-06-02**: local sandbox (Microsoft **MXC**, filesystem/network/process limits, Intune-enforceable) and **cloud** ephemeral Linux (`copilot --cloud`); local included in seat, cloud billed. Hooks: `preToolUse` can **deny**. `--allow-all` / yolo is a product switch, **not** a policy.

| Plane | Owns | **Pull** | **Drop** |
| --- | --- | --- | --- |
| Control | Repo/org policy, `permissions=` / hooks, interrupt map, MCP allowlist | 07 Rule of Two / lethal trifecta; 09 FS PDP **fail-open** caveat; 12 `interrupt_on` | “The model is the policy”; `permissions=` covering MCP/`execute` (09) |
| Data | Prompt from open files; tool loop; stream | 08/10 context; 11 subagents if parallel | `LocalShellBackend` in prod (09/13) |
| Persistence | Completions: **no** thread. Agent: `thread_id` + checkpointer to resume HITL | 13 | MemorySaver in prod |
| Sandbox | Isolation ≠ authorization | 09 Firecracker/gVisor; GitHub MXC analog | Creds inside guest |

**PDP vs HITL (the copilot sentence):** Draw **PDP first**, interrupt second. Anthropic coding-agent analog (12, not a Copilot SKU): users approve **~93%** of prompts; sandboxing cut prompts **84%**. Cheney: budget **issued** QPS including cancels (~**2×** shown ghost-text). They propagate Go `context` so the proxy can cancel the model; without cancel you pay for unused decode.

#### 2.5 Archetype D — Content moderation (UGC cascade)

**Not** a chatbot with a toxicity prompt. It is a **cascade + audit log + appeal**.

Published shape: Discord **AutoMod** keyword/regex **block before post**; AutoMod AI (OpenAI) **alerts** mods; **>45M** unwanted messages blocked before post. Patents: sync keyword vs async ML; ML budget **~40 ms** to feel realtime (US 11991133 / US 12489724 — **patent**, not a measured SLO). Reddit **AutoModerator**: YAML; 2021 transparency: AutoMod **58.9%** of moderator removals, **103.6M** pieces (CHI ModSandbox citing Reddit TR 2021). YouTube: **>500 hours/min** upload; **vast majority** actioned content first detected by automation, then humans; hash fingerprints for known violative re-uploads (CA AB587 Q4 2023). UniGuard-Cascade: DistilBERT fast path + LLM slow path, **87%** less LLM vs GPT-4-everywhere, **5.7×** cost (JCSSR). UNIVID (ACL 2026 industry): recall-and-rank **two** VLM variants + RAG over ~100k past violations. Secondary blog (tianpan): **97.5%** cheap path / **2.5%** frontier — **not** a vendor SLO; cascade intuition only.

| API | What it is | 2026 note |
| --- | --- | --- |
| OpenAI **omni-moderation** | Text+image; 13 categories; `flagged` + scores; **free**; treat scores as **signals**, not auto-block; streaming scores **after** full output | Not in monthly usage. Rate limits e.g. Free **250 RPM / 5k RPD / 10k TPM**. Images **20 MB**; **no audio** |
| **Perspective** | TOXICITY etc. | **Sunset 2026-12-31**; no migration support; new quotas stopped Feb 2026 |
| AWS **Comprehend** | `DetectToxicContent`; prompt safety | Trust & Safety **per 100 chars**, **min 3 units** |
| **Azure Content Safety** | Text/image records | HTML list prices often `$` gated. Microsoft Q&A: Standard **$0.38 / 1,000 text records** (1 record ≤ 1,000 Unicode chars). `> ⚠️ Gap` on the calculator page itself |

**CSAM / legal (high-level, no exploit):** US ESPs report apparent CSAM to NCMEC CyberTipline (**18 U.S.C. § 2258A**). Detection of **known** material is hash-matching (PhotoDNA, CSAI Match, PDQ) — **not** an LLM. LLM classifiers are for **policy** (hate, spam, nuance), not a substitute for hash + mandatory report. Do not design “the model looks at the image and decides.”

**EU DSA:** Art. 15 annual machine-readable transparency (automated means, accuracy/error, safeguards). Implementing Regulation **(EU) 2024/2835**: templates from **1 Jul 2025**; first full calendar cycle **1 Jan–31 Dec 2026**. Art. 17 statements of reasons → DSA Transparency Database. Art. 20 internal complaint-handling (appeals). VLOPs have extra Art. 24/42 cadence.

| Plane | Owns | **Pull** | **Drop** |
| --- | --- | --- | --- |
| Control | Policy version, thresholds, reviewer RBAC, appeal SLA | 07 PDP; 17 policy-as-artifact | LLM-only; Perspective as 2027 SoT |
| Data | Ingest → **hash/keyword → classifier → (optional) LLM → enqueue** | Cheap first | Judge/LLM on **every** item on the post clock |
| Persistence | Immutable decision log, evidence pointer, appeal trail | WORM (07/17) | Mutable “status” without history |
| Human | Queue, dual-control for CSAM viewers, wellbeing | HITL as **reviewer**, not model | Reviewer MCP with `include_values` of CSAM |

**Dual-model:** two independent classifiers (or UNIVID-Lite + UNIVID-RAG). Agree → auto-act if policy allows; disagree → human. Do **not** average scores into a fake consensus. Omni `flagged` is a **signal**; refusals can still flag.

#### 2.6 Interview checklist (one page)

- [ ] Corpus < 200k? Skip ANN.
- [ ] Tenant isolation = index **predicate**, not prompt.
- [ ] Latency class: completion vs chat vs batch.
- [ ] Guardrails + ZT MCP drawn in HLD, not wrap.
- [ ] HITL ≠ PDP (12). Model is never the PDP.
- [ ] Judge off user p99 (04/17).
- [ ] Dual-run `index_gen` / prompt hash (17).
- [ ] Capacity: QPS, concurrent sessions, embed ingest, eval tax.
- [ ] One cost cliff named (15 RU or 16 SC N=40).
- [ ] One wrong-archetype named (Common Failure Modes).

---

### 3. Token Economics & NFR Analysis

#### 3.1 List prices (reuse 16/17; all `$ / 1k` **[inferred]** from shape × rate)

| Model | In / cached / out per 1M | Source |
| --- | --- | --- |
| GPT-5.6 **Luna** | **$0.20 / $0.02 / $1.20** | 16/17 index of openai.com/api/pricing — HTML **JS-gated** (`> ⚠️ Gap`) |
| GPT-5.6 **Terra** | **$2 / $0.20 / $12** | same |
| GPT-5.6 **Sol** | **$5 / $0.50 / $30** | same |
| **Sonnet 4.6** | **$3** in / **$15** out; cache **1.25× write / 0.1× read** | 16/17, 03 |
| OpenAI **Fast mode** | **99.9%** uptime; **99%** of requests above N tok/s | **Throughput**, not product p99 |
| OpenAI **omni-moderation** | **$0** (rate-limited) | Help Center: free, not in monthly usage |

Formula: `$ / 1k = 1000 × (in_tok/1e6 × $in + out_tok/1e6 × $out)`.

**Streaming vs full-wait: same `$`.** You pay output tokens either way. Streaming buys **TTFT** and cancel (Copilot typed-through). Full-wait is only for batch / moderation LLM slow-path.

#### 3.2 Chatbot — **1k sessions**, 8 turns, 2,000 in / 400 out per turn (same task shape as 17)

| Variant | Arithmetic | **$ / 1k sessions** |
| --- | --- | --- |
| Luna, no retrieve | 8 × (2000×0.20 + 400×1.20) / 1e6 = 8 × $0.00088 | **$7.04** |
| Luna + RAG on **40%** of turns (+800 in) | + 8×0.4×800×0.20/1e6 = +$0.000512/session | **$7.55** |
| Luna, 1,500/2,000 prefix cached @ 0.1× | 8 × (1500×0.02 + 500×0.20 + 400×1.20) / 1e6 = 8 × $0.00061 | **$4.88** |
| Sonnet uncached | 8 × (2000×3 + 400×15) / 1e6 = 8 × $0.012 | **$96.00** |
| Sonnet, 1,500 prefix @ 0.1× read | 8 × (1500×0.30 + 500×3 + 400×15) / 1e6 = 8 × $0.00795 | **$63.60** |
| **Anti-pattern:** SC N=40 on the CoT shape (16) | 16: **~$780 / 1k** Sonnet **calls**, not sessions | **Never a chat default** |

Cache **write** 1.25× still applies on first fill (03). Do not sell skip-ANN as always cheaper — see search.

#### 3.3 Search — **1k queries** (stateless; 1 query = 1 “session”)

| Variant | Arithmetic | **$ / 1k** |
| --- | --- | --- |
| Terra generate 2,500 in / 400 out | (2500×2 + 400×12) / 1e6 = $0.0098 | **$9.80** |
| + ANN 4 GB tenant ns (15 Plan-1 analog) | 4 RU × $16/M × 1000 | **+$0.064** |
| + query embed 500 tok `3-small` @ $0.02/M (15) | 500×0.02/1e6 × 1000 | **+$0.010** |
| **Total Terra+index** | | **$9.87** |
| Luna generate same 2,500/400 | (2500×0.20 + 400×1.20) / 1e6 = $0.00098 | **$0.98** + **$0.074** index |
| **Skip-ANN:** 200k corpus stuffed, Luna cached in | 200k × $0.02/1e6 = $0.004/query + 400 out $0.00048 | **$4.48** |
| Skip-ANN Sonnet cached in @ 0.1× | 200k × $0.30/1e6 = $0.06/query | **$60** corpus **alone** |

Interview point: Anthropic skip-RAG is **simplicity + quality** on a **small static** KB, not always cheaper. At high QPS, **$60 / 1k** Sonnet stuffed vs **~$10 / 1k** Terra+RAG — **retrieval can win on `$` even under 200k** if you query often.

**RU cliff (cite 15, do not recopy):** 20M × 1536-d at 50 QPS, one fat namespace **~$263k/mo** vs tenant namespaces **~$8.3k/mo** **[inferred, Plan 1]**.

#### 3.4 Copilot — **1k agent sessions** vs **1k completions**

| Shape | Arithmetic | **$ / 1k** |
| --- | --- | --- |
| **Agent session:** 12 tool loops, 3,000 in / 200 out Terra each | 12 × (3000×2 + 200×12) / 1e6 = 12 × $0.0084 = $0.1008/session | **$100.80** |
| Same, Luna | 12 × (3000×0.20 + 200×1.20) / 1e6 = 12 × $0.00084 | **$10.08** |
| **Tool-loop blowup:** 30 loops (uncapped) Terra | 30/12 × $100.80 | **$252** |
| Prefix-cache 2,000 static tools @ Terra 0.1×, 12 loops | 12 × (2000×0.20 + 1000×2 + 200×12) / 1e6 = 12 × $0.0048 | **$57.60** |
| **Completions:** 500 in / 50 out Luna | (500×0.20 + 50×1.20) / 1e6 = $0.00016 | **$0.16 / 1k completions** |
| Completions Terra | (500×2 + 50×12) / 1e6 = $0.0016 | **$1.60 / 1k completions** |

Cheney: ~half of completion **requests** are cancelled (typed-through). You still pay the provider for work already decoded unless the proxy cancels the model. Budget **issued** QPS, not **shown** ghost-text QPS.

#### 3.5 Moderation — **1k UGC items**, ~100 tokens / ~400 chars

| Path | Arithmetic | **$ / 1k items** |
| --- | --- | --- |
| OpenAI omni-moderation | $0 + RPM/TPM | **$0** |
| Comprehend toxicity, **min 3 units** @ **$0.0005 / 100 chars** | 1000 × $0.0015 | **$1.50** |
| Azure Content Safety **$0.38 / 1k records** (Q&A; 1 record ≤ 1k chars) | 1 record/item | **$0.38** |
| **LLM-every** Luna 800 in / 150 out | 1000 × (800×0.20 + 150×1.20) / 1e6 = 1000 × $0.00034 | **$0.34** |
| LLM-every Sonnet | 1000 × (800×3 + 150×15) / 1e6 = 1000 × $0.00465 | **$4.65** |
| Cascade **2.5%** LLM Luna (tianpan shape; **not** a SLO) | 0.025 × $0.34 | **$0.0085** + classifier |
| Dual-model: 2× Azure records | | **$0.76** |
| UniGuard-class **87%** LLM reduction vs GPT-4-everywhere | qualitative; their **5.7×** | cite paper, don’t invent `$` |

Omni **$0** is not infinite QPS: Free **250 RPM** is ~4 QPS; even Tier-class **5k RPM** is ~**83 QPS** — **bulkhead** a free signal API.

#### 3.6 Eval tax (04/17) — all archetypes

Online judge on the **user** path inflates p99. 17 CI Luna-m3 **$2.32 / 1k** vs Sonnet-m3 **$21.13 / 1k** **[inferred]**. 100% shadow on 1M Luna req/mo **+$880/mo**. Coverage% is an NFR, not a feeling. 5% same-SKU canary **~$0** extra tokens (17).

#### 3.7 Latency p50 / p95 / p99 per archetype

> ⚠️ Gap: no vendor publishes product-level percentiles for “support chatbot” or “enterprise RAG.” Copilot **mean < 200 ms** is Cheney’s completion path, **not** p99, **not** agent sessions. Discord **~40 ms** is a **patent** ML budget, not a measured SLO. Do **not** invent GitHub Copilot p99 80 ms.

**[inferred] architecture-derived policy** (compose 01 retrieve, 15 ANN, 16 extract, 17 flag SDK):

| Archetype | Clock | p50 | p95 | p99 | Mitigations |
| --- | --- | --- | --- | --- | --- |
| **Chatbot** | TTFT (stream) | **400 ms** | **1,200 ms** | **3,000 ms** | Prefix cache (03); skip retrieve on chitchat (01 Adaptive-RAG); never wait on judge |
| | First **useful** answer e2e | **2,000 ms** | **6,000 ms** | **12,000 ms** | Align with 16 extract **1,200 / 4,000 / 12,000 ms**; hop cap 1–2 |
| **Search** | Retrieve+rerank (01/15) | **80–400 ms** | **120 ms–1.5 s** | **250 ms–3 s** then fail-closed | 15 ANN **40–80 / 120 / 250 ms** policy + 01 retrieve timeout **200–500 ms**; hybrid **in parallel** |
| | Answer e2e | **1,300 ms** | **4,200 ms** | **12,300 ms** | Generate dominates; circuit-break index **independently** of FM (01) |
| **Copilot completions** | Ghost-text RTT | **mean < 200 ms** (Cheney, **published mean**) | `> ⚠️ Gap` | `> ⚠️ Gap` | HTTP/2 cancel; colocate; **do not** quote p99 80 ms |
| | Completions **policy** if you must SLO | **150 ms** | **350 ms** | **800 ms** | Hedged region; cancel typed-through; this is **policy**, not GitHub |
| **Copilot agent** | One tool-loop wall | **8 s** | **45 s** | **180 s** | Hop cap; sandbox warm pool (09); HITL is a **different clock** (queue SLA hours, not p99) |
| **Moderation** | Keyword/hash | **< 10 ms** | **20 ms** | **50 ms** | In-process |
| | Classifier (patent/Discord-class) | **20 ms** | **40 ms** | **100 ms** | Async if > budget; don’t block post on LLM |
| | LLM slow-path | **800 ms** | **2,500 ms** | **8,000 ms** | Queue, not inline |
| **Control (all)** | Flag SDK (17) | **1 ms** | **10 ms** | **50 ms** | Fail-open last bundle |

Cheney mean **< 200 ms** vs completions **policy** p50 **150 ms**: the policy row is what **you** contract if the interviewer demands percentiles; it is **not** a claim that GitHub publishes p50. If mean < 200 ms is the only published number, say so and refuse to invent p99.

#### 3.8 Throughput (interview arithmetic)

**Chatbot B2B [inferred, stated]:** 10k DAU, 3 sessions/user/day, 8 turns → 240k turns/day → **avg 2.8 QPS**, **peak ×10 = 28 QPS**. Concurrent sessions: 30k sessions/day × 3 min / 1440 min ≈ **63** avg, **~600** peak. Embed ingest: 500 tenants × 20 new docs/day × 20 chunks × 400 tok = 80M tok/day → Batch embed (15). Judge: **0** on this QPS; sidecar 1–5% (04). Envelope **2.8 → 28 QPS**.

**Search [inferred]:** reuse 15 **50 QPS** / 20M chunks / 5 tenants. Ingest WU ≠ query RU. Dual-run indexes during `model_id` change (17).

**Copilot completions [published]:** 400M/day ≈ **4,630 QPS avg**, peak **8,000 RPS**. Size **issued** QPS including cancels (~2× shown). Agentic QPS is **orders of magnitude** lower (human think time).

**Moderation [inferred]:** 1M comments/day ≈ **11.6 QPS** classifier. YouTube **500 h/min** is **upload ingest**, not comment QPS — size video hash farm separately. Human queue: if 2.5% escalate and 1 reviewer handles 200 items/hour, 1M × 0.025 / 200 ≈ **125 reviewer-hours/day**.

**LangChain GTM anecdote (13, traffic shape not p99):** ~**10k req/week**, **>150** users, **26%** interactive / **74%** ambient — not L1 support chat.

#### 3.9 Availability, RPO/RTO, compliance

**Availability:** OpenAI Fast mode **99.9%** is **throughput** (tok/s + uptime), not chatbot p99. Bedrock Reserved **99.5%** is **capacity**, not a registry and not your product SLO (17). Pinecone Starter/Builder have **no SLA** (15). Product availability = app tier × model API × index × (optional) sandbox. Bulkhead retrieve TPM ≠ generate TPM ≠ eval-job pool so a judge stampede cannot take chat (01, 17).

**RPO/RTO [inferred] per archetype:**

| Archetype | RPO | RTO | Trade-off |
| --- | --- | --- | --- |
| **Chatbot** | Last **checkpoint** (13) | Thread = Postgres; API replicas do **not** run the graph in split mode (13) | Checkpoint every turn vs `$`/write; disconnect ≠ cancel |
| **Search** | Last compacted slab | **Alias flip minutes**; rebuild **hours** (15) | Dual-run `index_gen` during migrate vs serving `latest` |
| **Copilot completions** | None (stateless) | Hedge region; cancel is the SLO lever | No thread to restore |
| **Copilot agent** | Last checkpoint + sandbox TTL | Resume HITL; you **expire-deny** (12) — LangGraph waits **indefinitely** | Expire-**approve** is a confused deputy |
| **Moderation** | Last **WORM** append | Replay queue; losing the appeal log is a **compliance** incident (DSA Art. 17) | Sync fast path vs async LLM; hash DB RPO is legal |

**Compliance trade-offs (drop in the wrap):**

| Label | What it forces | What it is not |
| --- | --- | --- |
| **HIPAA** | BAA, PII **before embed** (15 Vec2Text **92% exact** / **89% MIMIC names**), CMEK, no PHI in Anthropic schema (16) | Quantization ≠ HIPAA; omni-moderation ≠ a BAA |
| **GDPR** | Delete blob **and** digest (17 LangSmith indefinite goldens); residency | “We hashed it” without a deletion story |
| **DSA** (EU UGC) | Art. 15 accuracy/error **and safeguards**; Art. 17 SoR; Art. 20 appeals; 2026 template cycle | Keyword-only has nothing accurate to report |
| **§ 2258A** (US ESP) | Hash-first known CSAM + CyberTipline | LLM-as-CSAM-detector |
| **SOC2** | Tenant isolation = predicate; WORM of allow\|deny | Prompt that says “don’t show HR” |

---

### 4. Distributed Resilience & Security

#### 4.1 Durable execution per archetype

| Archetype | Durable state | Runtime | Failure if wrong |
| --- | --- | --- | --- |
| **Chatbot** | `thread_id` + checkpointer (13); optional Store; memory **profile** as batch (Dreaming) | Agent Server: API replicas **do not** run the graph in split mode; queue workers + Postgres + Redis **signaling only** (13) | InMemorySaver; memory write on every turn; disconnect = cancel |
| **Search** | **Stateless query**; durable **index** + alias + ACL bitmap (15); ingest watermark | Ingest workers ≠ query replicas (01) | Query p99 tracks reindex; hybrid under consistency `ONE` cites deleted docs (01 Weaviate QUORUM) |
| **Copilot** | Completions: **no** thread. Agent: checkpointer **required** for HITL (12); sandbox TTL; git worktree / cloud session | Cancel completion via H2 stream reset (Cheney). HITL: you expire-deny (12) | Host shell; expire-**approve**; `permissions=` as MCP gateway |
| **Moderation** | **Kafka/queue** + **immutable** decision log + appeal; hash DB | Sync fast path; async LLM/human | LLM on the post path; mutable status; reviewer laptop with raw CSAM |

**Dual-running bundles (17):** a release is `(model_id, prompt_hash, schema_hash, decoding_params, index_gen, eval_suite_id)`. Sticky `thread_id` during canary. Changing `index_gen` without **dual-running indexes** is a retrieval incident. 5% same-SKU canary **~$0** extra tokens; 100% shadow **+$880/mo** on 1M Luna **[inferred]**. Rollback = **retag**, not rebuild Agent Server (17: prompt-only → flag).

#### 4.2 Failure taxonomy

| Class | Example | Retry? | What you draw |
| --- | --- | --- | --- |
| **Wrong-archetype** | Host shell on copilot; LLM-every on UGC; stuffed Sonnet at 50 QPS | Never — redesign | Name it in the wrap (table below §6) |
| **Transient 429 / 5xx** | FM TPM, index hot partition | Yes + **full jitter**; circuit closed → open → half-open | Independent breakers: **index ≠ FM**; **classifier ≠ LLM queue** |
| **Poison golden / dim mismatch** | Eval suite with PAN; embed dim ≠ schema | **No** retry | 4xx; fail CI (17); do not skip merge |

Idempotency: ingest `chunk_id` (15); moderation `item_id` + policy_hash; HITL re-hash tool args before resume (07/12 TOCTOU).

**Circuit breaker (closed → open → half-open):**

- **Retrieve / index** independently of the **foundation model** (01, 15). Index 5xx must not freeze generate; generate 429 must not skip ACL retrieve.
- **Moderation:** classifier vs **LLM queue** independently. Hash path is **fail-closed** (CSAM). Spam fail-open vs fail-closed is a **product NFR** — say which.
- Half-open: **canary**, not the unsafe user blob / not CSAM bytes.

**Fallbacks:**

| Path | Fallback | Refuse if |
| --- | --- | --- |
| Search / RAG chat | Last-good retrieve cache → **BM25-only** → `retrieval_degraded` | Policy forbids ungrounded (01) |
| Bundle / flags | Last-good bundle (17 fail-open flags) | Serving `latest` |
| Sandbox pool empty | **Never** host exec (07/09) | — |
| Moderation classifier 5xx | **Fail-closed** on CSAM/hash; product choice on spam | LLM as CSAM control |
| Completions | Hedge region; cancel typed-through | Waiting for agent p99 |

Hedged retrieve; cancel loser (01). Sandbox **warm pool**. Spend ledger fail-closed on uncapped tool loop (07).

#### 4.3 Tenancy, PII, EchoLeak

- **Chatbot + search:** tenant in JWT → namespace / RLS / bitmap **pre-filter**. Microsoft Copilot: index “only surfaces results if the user already has access.” EchoLeak (**CVE-2025-32711**, CVSS **9.3**, 07) is zero-click XPIA through **retrieved** email — RAG is an untrusted-input leg (Rule of Two **[A]**). “The prompt said don’t show HR” is not a control.
- **Copilot:** repo/org IdP; hooks + PDP; sandbox does not mint OAuth. Rule of Two: split reader/writer sessions or HITL on [C].
- **Moderation:** tenant = community/server; **reviewer ACL ≠ user ACL**. CSAM viewers: dual-control, no MCP `fetch_values`, minimized retention.

**PII: detect → redact → audit before embed** (15). Vec2Text: **92% exact** on 32 tokens; **89% MIMIC names**. Before goldens enter git/Hub (17 LangSmith **indefinite**). Before HITL UI (12). Regex + NER; fail-closed PAN. Redacting in the **prompt** after embedding is too late (15). Never log CSAM bytes or raw PII — log **hashes** and kinds.

#### 4.4 Zero-Trust MCP on **every** archetype (this file — no module 19)

Minimum (07/09/15/17): **OAuth 2.1 + PKCE**, **RFC 8707** resource indicator = this MCP server, **RFC 8693 no passthrough**, **hash-pin** tools + server digest, gateway PEP, identity from **verified token**. CVE-2025-54136 if you skip re-hash (MCPoison); CVE-2025-6514 if you `open()` hostile auth metadata. MCP is **not** the PDP.

| Archetype | MCP verbs behind PEP | Deny |
| --- | --- | --- |
| **Chatbot** | `retrieve_public_kb`, `retrieve_hr`, `create_ticket` (HITL) | Host `bash`; ns from tool args |
| **Search** | `query_index` (ns from JWT) | **`include_values`** for assistants (15 invert surface) |
| **Copilot** | `execute` **in sandbox only**; `preToolUse` deny | Host `bash`; `permissions=` as gateway |
| **Moderation** | `enqueue_review`, `record_decision` | `set_alias` production; `fetch_values` of CSAM |
| **All** | `eval.run` **staging only**; `promote_tag` HITL (17) | Assistant `promote_tag` / `registry.set_alias` |

**Tool RBAC:** least privilege per verb. `create_ticket` / `refund` / `execute` / `promote_tag` require **PDP then HITL**. Schema-valid tool JSON is **not** authorization (16). `k` and `include_values=false` are policy, not model suggestions.

**Immutable decision logs:** `(ts, actor_id, item_id|thread_id, action, policy_version|bundle, scores, prev_hash)`. Reviewer ≠ user ACL. Appeals: DSA Art. 20; YouTube strike appeal. Median time-to-decision is a **reported** metric (Art. 15(1)(d)).

---

### 5. Production Enterprise Code

Self-contained stdlib. Illustrates the **round’s control flow**, not a chatbot: NFR fork (**skip ANN if `corpus_tokens < 200_000`**), sticky bundle on `thread_id`, circuit breaker on **retrieve independently of generate**, fallback **BM25**, PII detect→redact→audit **before embed**, MCP deny **`include_values`** and **host shell**. Retries + full jitter. Structured logs with **hashes**. Never log CSAM/PII. Run: `python ai_system_design_runtime.py`.

```python
#!/usr/bin/env python3
"""Interview-round control flow: NFR fork, sticky bundle, dual breakers, MCP deny.

Not a chatbot. Admit → fork → retrieve/generate isolation → MCP PEP.
Run: python ai_system_design_runtime.py
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

SKIP_ANN_TOKENS = 200_000
BUNDLE_PIN = ("model_id", "prompt_hash", "schema_hash", "decoding_params", "index_gen", "eval_suite_id")


class CorrelationFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        for k, d in (
            ("correlation_id", "-"),
            ("tenant_id", "-"),
            ("bundle_hash", "-"),
            ("thread_id", "-"),
        ):
            setattr(record, k, getattr(record, k, d))
        return True


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("ai_sd_round")
    if logger.handlers:
        return logger
    h = logging.StreamHandler()
    h.setFormatter(
        logging.Formatter(
            '{"ts":"%(asctime)s","level":"%(levelname)s","cid":"%(correlation_id)s",'
            '"tenant":"%(tenant_id)s","bundle":"%(bundle_hash)s",'
            '"thread":"%(thread_id)s","msg":"%(message)s"}'
        )
    )
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


class PermanentGenerateError(RuntimeError):
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


EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
PAN_RE = re.compile(r"\b(?:\d[ -]*?){13,19}\b")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def pii_detect_redact_audit(
    text: str,
    *,
    audit: list[dict[str, Any]],
    correlation_id: str,
    tenant_id: str,
    sink: str,
    block_on_pan: bool = True,
) -> str:
    kinds = [k for k, rx in (("email", EMAIL_RE), ("pan", PAN_RE)) if rx.search(text)]
    pre = _sha(text)
    row = {"cid": correlation_id, "tenant": tenant_id, "sink": sink, "kinds": kinds, "detector": "regex"}
    if "pan" in kinds and block_on_pan and sink in {"embed_ingest", "mcp_args"}:
        audit.append({**row, "action": "block-from-embed", "pre": pre, "post": _sha("")})
        raise PermissionError(f"pii_block:{sink}:pan")
    redacted = EMAIL_RE.sub(
        lambda m: f"[EMAIL_{hashlib.sha256(m.group().encode()).hexdigest()[:12]}]",
        text,
    )
    redacted = PAN_RE.sub("[PAN]", redacted)
    audit.append({**row, "action": "redact" if redacted != text else "allow", "pre": pre, "post": _sha(redacted)})
    return redacted


@dataclass(frozen=True)
class AuthContext:
    tenant_id: str
    principal: str
    roles: frozenset[str]
    thread_id: str


@dataclass(frozen=True)
class Bundle:
    model_id: str
    prompt_hash: str
    schema_hash: str
    decoding_params: str
    index_gen: str
    eval_suite_id: str

    def digest(self) -> str:
        blob = "|".join(getattr(self, k) for k in BUNDLE_PIN)
        return "b_" + hashlib.sha256(blob.encode()).hexdigest()[:16]


@dataclass
class TurnResult:
    answer: str
    source: str
    degraded: bool
    skip_ann: bool
    bundle_hash: str
    include_values: bool = False


class BM25Index:
    def __init__(self) -> None:
        self.df: dict[str, int] = defaultdict(int)
        self.postings: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self.doclen: dict[str, int] = {}

    def upsert(self, doc_id: str, text: str) -> None:
        toks = text.lower().split()
        self.doclen[doc_id] = max(1, len(toks))
        seen: set[str] = set()
        for tok in toks:
            self.postings[tok][doc_id] += 1
            if tok not in seen:
                self.df[tok] += 1
                seen.add(tok)

    def search(self, query: str, k: int) -> list[tuple[str, float]]:
        toks = query.lower().split()
        scores: dict[str, float] = defaultdict(float)
        n = max(1, len(self.doclen))
        avgdl = sum(self.doclen.values()) / n
        for tok in toks:
            df = self.df.get(tok, 0)
            idf = max(0.0, ((n - df + 0.5) / (df + 0.5)))
            for doc_id, tf in self.postings.get(tok, {}).items():
                dl = self.doclen[doc_id]
                scores[doc_id] += idf * (tf * 2.2) / (tf + 1.2 * (0.25 + 0.75 * dl / avgdl))
        return sorted(scores.items(), key=lambda x: x[1], reverse=True)[:k]


class ScriptedIndex:
    def __init__(self, fail_kind: str | None = None) -> None:
        self.fail_kind = fail_kind
        self.docs: dict[str, dict[str, str]] = defaultdict(dict)

    def upsert(self, ns: str, doc_id: str, text: str) -> None:
        self.docs[ns][doc_id] = text

    def query(self, ns: str, query: str, k: int) -> list[tuple[str, float]]:
        if self.fail_kind == "transient":
            raise TimeoutError("index_timeout")
        scored = []
        q = set(query.lower().split())
        for did, text in self.docs[ns].items():
            overlap = len(q & set(text.lower().split()))
            scored.append((did, float(overlap)))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:k]


class ScriptedFM:
    def __init__(self, fail_kind: str | None = None) -> None:
        self.fail_kind = fail_kind

    def generate(self, prompt_hash: str, context: str) -> str:
        if self.fail_kind == "transient":
            raise TimeoutError("fm_timeout")
        if self.fail_kind == "permanent":
            raise PermanentGenerateError("schema_mismatch")
        return f"ok:{prompt_hash[:8]}:{_sha(context)[:8]}"


MCP_ALLOW = frozenset({"retrieve_kb", "query_index", "create_ticket", "record_decision"})
MCP_DENY = frozenset({"include_values", "host_shell", "bash", "fetch_values", "set_alias", "promote_tag"})


@dataclass
class DesignRuntime:
    """Round control flow. Production: OpenAI + Pinecone/pgvector + LangGraph checkpointer."""

    corpus_tokens: int
    champion: Bundle
    index: ScriptedIndex = field(default_factory=ScriptedIndex)
    fm: ScriptedFM = field(default_factory=ScriptedFM)
    bm25: BM25Index = field(default_factory=BM25Index)
    retrieve_breaker: CircuitBreaker = field(default_factory=lambda: CircuitBreaker("retrieve_index"))
    generate_breaker: CircuitBreaker = field(default_factory=lambda: CircuitBreaker("foundation_model"))
    sticky: dict[str, Bundle] = field(default_factory=dict)
    audit: list[dict[str, Any]] = field(default_factory=list)
    last_good_ids: dict[str, list[str]] = field(default_factory=dict)

    def namespace_from_auth(self, auth: AuthContext) -> str:
        return f"tenant-{auth.tenant_id}"

    def resolve_bundle(self, auth: AuthContext) -> Bundle:
        pinned = self.sticky.get(auth.thread_id)
        if pinned is not None:
            return pinned
        self.sticky[auth.thread_id] = self.champion
        return self.champion

    def mcp_call(self, verb: str, args: dict[str, Any], auth: AuthContext) -> dict[str, Any]:
        if verb in MCP_DENY or args.get("include_values") is True:
            slog(logging.WARNING, f"mcp_deny verb={verb}", correlation_id="-", tenant_id=auth.tenant_id, bundle_hash="-", thread_id=auth.thread_id)
            raise PermissionError(f"mcp_deny:{verb}")
        if verb not in MCP_ALLOW:
            raise PermissionError(f"mcp_unknown:{verb}")
        if verb == "create_ticket" and "refund" not in auth.roles:
            raise PermissionError("pdp_deny:create_ticket")
        ns = self.namespace_from_auth(auth)
        return {"ok": True, "verb": verb, "ns": ns, "args_hash": _sha(json.dumps(args, sort_keys=True))}

    def ingest(self, auth: AuthContext, doc_id: str, text: str, *, correlation_id: str) -> None:
        if "ingest" not in auth.roles:
            raise PermissionError("pdp_deny:ingest")
        ns = self.namespace_from_auth(auth)
        clean = pii_detect_redact_audit(
            text, audit=self.audit, correlation_id=correlation_id, tenant_id=auth.tenant_id, sink="embed_ingest"
        )
        self.index.upsert(ns, doc_id, clean)
        self.bm25.upsert(f"{ns}:{doc_id}", clean)
        slog(
            logging.INFO,
            f"ingest_ok doc={doc_id} text_sha={_sha(clean)[:12]}",
            correlation_id=correlation_id,
            tenant_id=auth.tenant_id,
            bundle_hash="-",
            thread_id=auth.thread_id,
        )

    def _retrieve(self, ns: str, query: str, k: int) -> tuple[list[str], str, bool]:
        skip_ann = self.corpus_tokens < SKIP_ANN_TOKENS
        if skip_ann:
            ids = [f"stuffed:{ns}"]
            return ids, "stuffed", False
        self.retrieve_breaker.allow()

        def _ann() -> list[tuple[str, float]]:
            return self.index.query(ns, query, k)

        try:
            ranked = retry_call(_ann)
            self.retrieve_breaker.record_success()
            ids = [d for d, _ in ranked]
            self.last_good_ids[ns] = ids
            return ids, "ann", False
        except (TimeoutError, ConnectionError, CircuitOpenError):
            self.retrieve_breaker.record_failure()
            bm = self.bm25.search(query, k)
            if bm:
                return [d.split(":", 1)[-1] for d, _ in bm], "bm25", True
            cached = self.last_good_ids.get(ns) or []
            if cached:
                return cached, "last_good_cache", True
            return [], "retrieval_degraded", True

    def _generate(self, bundle: Bundle, context: str) -> str:
        self.generate_breaker.allow()

        def _call() -> str:
            return self.fm.generate(bundle.prompt_hash, context)

        try:
            out = retry_call(_call)
            self.generate_breaker.record_success()
            return out
        except PermanentGenerateError:
            self.generate_breaker.record_failure()
            raise
        except (TimeoutError, ConnectionError, CircuitOpenError):
            self.generate_breaker.record_failure()
            raise

    def handle(self, auth: AuthContext, user_text: str, *, correlation_id: str, k: int = 5) -> TurnResult:
        bundle = self.resolve_bundle(auth)
        bhash = bundle.digest()
        ns = self.namespace_from_auth(auth)
        clean = pii_detect_redact_audit(
            user_text, audit=self.audit, correlation_id=correlation_id, tenant_id=auth.tenant_id, sink="query"
        )
        ids, source, degraded = self._retrieve(ns, clean, k)
        if source == "retrieval_degraded" and not ids:
            slog(
                logging.WARNING,
                "retrieval_degraded_refusal",
                correlation_id=correlation_id,
                tenant_id=auth.tenant_id,
                bundle_hash=bhash,
                thread_id=auth.thread_id,
            )
            return TurnResult("ungrounded_refused", source, True, self.corpus_tokens < SKIP_ANN_TOKENS, bhash)
        ctx_hash = _sha("|".join(ids))
        answer = self._generate(bundle, ctx_hash + clean)
        slog(
            logging.INFO,
            f"turn_ok source={source} degraded={degraded} skip_ann={self.corpus_tokens < SKIP_ANN_TOKENS} ctx={ctx_hash[:12]}",
            correlation_id=correlation_id,
            tenant_id=auth.tenant_id,
            bundle_hash=bhash,
            thread_id=auth.thread_id,
        )
        return TurnResult(answer, source, degraded, self.corpus_tokens < SKIP_ANN_TOKENS, bhash)


def build_runtime(corpus_tokens: int) -> DesignRuntime:
    champ = Bundle("luna", "p_aaa", "s_bbb", "t0", "idx_1", "eval_v1")
    return DesignRuntime(corpus_tokens=corpus_tokens, champion=champ)


if __name__ == "__main__":
    small = build_runtime(50_000)
    ingest = AuthContext("acme", "worker", frozenset({"ingest"}), "t0")
    user = AuthContext("acme", "ada", frozenset({"search"}), "thread-1")
    small.ingest(ingest, "c1", "Reset MFA via the security desk. Contact ada@example.com", correlation_id="cid-1")
    r1 = small.handle(user, "how do I reset MFA? ping ada@example.com", correlation_id="cid-2")
    print(r1)
    assert r1.skip_ann is True and r1.source == "stuffed" and r1.degraded is False
    r1b = small.handle(AuthContext("acme", "ada", frozenset({"search"}), "thread-1"), "again", correlation_id="cid-2b")
    assert r1b.bundle_hash == r1.bundle_hash
    try:
        small.mcp_call("query_index", {"include_values": True}, user)
        raise SystemExit("include_values should deny")
    except PermissionError:
        pass
    try:
        small.mcp_call("host_shell", {"cmd": "ls"}, user)
        raise SystemExit("host_shell should deny")
    except PermissionError:
        pass
    large = build_runtime(2_000_000)
    large.ingest(ingest, "c1", "Reset MFA via the security desk.", correlation_id="cid-3")
    large.index.fail_kind = "transient"
    large.retrieve_breaker = CircuitBreaker("retrieve_index", failure_threshold=1, cooldown_s=60)
    r2 = large.handle(user, "reset MFA", correlation_id="cid-4")
    print(r2)
    assert r2.skip_ann is False and r2.degraded is True and r2.source == "bm25"
    assert any(row.get("action") in {"redact", "allow", "block-from-embed"} for row in small.audit)
    assert not any("ada@example.com" in json.dumps(row) for row in small.audit)
    print("ok", len(small.audit) + len(large.audit), "audit rows")
```

**Wiring notes (not in the script):** production Pinecone `query(namespace=namespace_from_auth(auth), include_values=False, filter=...)` — namespace **not** from MCP args. Hybrid fusion policy is **01**. Gateway: OAuth 2.1, RFC 8707 audience = this MCP server, hash-pin tool JSON, **no** passthrough to OpenAI/Pinecone. LangGraph checkpointer on `thread_id` (13); disconnect does not cancel. Judge sidecar (04). Dual-run `index_gen` on embed change (17). Log `(cid, bundle_hash, source, skip_ann)` — never bodies, never CSAM.

---

### 6. Architectural System Design Scenarios

Exactly **two** scored scenarios. Copilot is a **delta** after scenario 2 if the interviewer swaps — not a third matrix.

#### Scenario 1 — Multi-tenant B2B support assistant (chat + RAG)

**Problem.** SaaS help-center. **500 tenants.** Each tenant: manuals + SKU tables + error codes (`TS-999`). Users: customer (public KB only) and CS agent (public + account tools). NFRs: tenant isolation SOC2, p95 “a few seconds,” exact-ID **and** paraphrase, no GraphRAG (factoid/FAQ). Peak ~**28 QPS** turns (§3.8). Some tenants have **<200k tok** KB; a few have millions of chunks.

**Proposed architecture (recommended B):**

```
  ┌─────────┐   ┌─────────────────────────────────────────────────────────┐
  │ Entra   │──▶│ CONTROL: JWT → tenant_id + role   flags → bundle (17)   │
  │ JWT     │   │   hop cap=2   PDP: refund=HITL   MCP allowlist hash-pin │
  └─────────┘   └──────────────────────────┬──────────────────────────────┘
                                           ▼
                    ┌──────────────────────────────────────────────────────┐
                    │ DATA: rails → router (chitchat | id | faq)           │
                    │   id/faq → ACL bitmap → hybrid BM25+dense (01/15)    │
                    │   → rerank → generate+cite   chitchat → no retrieve  │
                    │   tools: retrieve_kb(ns=jwt) | create_ticket (HITL)  │
                    │          | refund (interrupt)                        │
                    └───────────────┬──────────────────┬───────────────────┘
                                    ▼                  ▼
                    ┌─────────────────────┐  ┌─────────────────────────────┐
                    │ PERSIST: thread     │  │ TELEMETRY: nDCG@k golden    │
                    │ checkpointer (13)   │  │   traces ACL-redacted  RU   │
                    │ index alias per ns  │  │   judge SIDECAR (04)        │
                    └─────────────────────┘  └─────────────────────────────┘
```

**Trade-off matrix:**

| Axis | **A. Stuff all KBs + one Sonnet** | **B. Hybrid + ACL ns + Luna/Terra route (recommended)** | **C. Per-tenant always-on agent (uncapped tools)** |
| --- | --- | --- | --- |
| **Cost** | Small tenants: skip-ANN OK; large: **$60 / 1k** stuffed Sonnet **[inferred]** | Luna session **$7.04–$7.55 / 1k**; Terra FAQ **$9.87 / 1k**; 15 ns split avoids **~$263k/mo** vs **~$8.3k/mo** | Tool-loop **$100.80 / 1k** Terra **[inferred]**; hop blowup **$252** at 30 loops |
| **Latency** | Prefill 200k dominates TTFT; cache helps **>2×** (Anthropic) | e2e **[inferred] p50 2s / p95 6s / p99 12s**; retrieve timeout 200–500 ms policy (01) | Agent p95 **45 s** class — wrong for L1 chat |
| **Ops** | Simple until one tenant exceeds 200k | Dual-run `index_gen`; ingest ≠ query | Agent Server + sandbox for tickets |
| **Security** | Cross-tenant if one prompt; no ACL bitmap | Predicate + scoped MCP `retrieve_*`; EchoLeak = untrusted retrieve | Refund without PDP = confused deputy |
| **Scalability** | QPS × 200k tokens = `$` cliff | 50 QPS-class proven as 15 Plan 1; peak **28 QPS** turns | Ambient 74% GTM shape (13) is not this |

**Decision.** **B.** Per tenant: if KB **< ~200k and static**, skip ANN **for that tenant** (Anthropic) and cache; if larger or multi-user ACL, hybrid + **namespace-per-tenant** (01/15). Route chitchat vs factoid. Stream. Hop cap **2**. Terra (or Sonnet) only for cited hard FAQ; Luna for chitchat (**$7.04** no-RAG / **$7.55** +40% RAG **[inferred]**). Refund/ticket = **PDP + HITL** (12), not “the model may.” Judge sidecar. Dual-run indexes on embed change (17). I would not stuff all tenants into one Sonnet prompt. I would not ship an uncapped deep agent as L1.

#### Scenario 2 — UGC moderation platform (classifier + LLM + human + appeal)

**Problem.** Consumer community, **1M comments/day** (~12 QPS) + image posts. Policies: spam, hate, sexual, **CSAM (known-hash)**, appeals for EU users (DSA). Cannot put an LLM on every post. Reviewers must not have unconstrained MCP. Perspective is **dead 2026-12-31**.

**Proposed architecture (recommended B):**

```
  ┌─────────┐   ┌─────────────────────────────────────────────────────────┐
  │ Upload  │──▶│ CONTROL: policy_hash  thresholds  reviewer RBAC         │
  │ + IdP   │   │   DSA report job   2258A CyberTipline on hash hit       │
  └─────────┘   └──────────────────────────┬──────────────────────────────┘
                                           ▼
                    ┌──────────────────────────────────────────────────────┐
                    │ DATA: PhotoDNA/CSAI/PDQ hash → keyword AutoMod       │
                    │   → omni-moderation + (optional) 2nd classifier      │
                    │   agree+high → auto-action    disagree/mid → queue   │
                    │   LLM slow-path ONLY on mid-confidence text (async)  │
                    └───────────────┬──────────────────┬───────────────────┘
                                    ▼                  ▼
                    ┌─────────────────────┐  ┌─────────────────────────────┐
                    │ PERSIST: WORM       │  │ TELEMETRY: P/R by policy    │
                    │ decision + evidence │  │   queue SLA  Art.15 fields  │
                    │ pointer + appeal    │  │   omni RPM bulkhead         │
                    └─────────────────────┘  └─────────────────────────────┘
```

**Trade-off matrix:**

| Axis | **A. LLM-every (Sonnet policy prompt)** | **B. Cascade hash+classifier+async LLM+human (recommended)** | **C. Keyword-only AutoMod** |
| --- | --- | --- | --- |
| **Cost** | **$4.65 / 1k** Sonnet **[inferred]** → **$4,650/day** at 1M | Omni **$0** + 2.5% Luna **~$8.50/day** + Azure/Comprehend **$0.38–$1.50 / 1k**; UniGuard **87%** LLM cut | YAML cheap; misses paraphrase |
| **Latency** | LLM p99 **8 s [inferred]** on **post** path — Discord-class needs **~40 ms** ML | Fast path **p50 20 / p95 40 / p99 100 ms [inferred]**; LLM **queued** | Inline microseconds |
| **Ops** | Simple, doesn’t scale | Threshold calibration; dual-run **policy_hash** (17); reviewer staffing **~125 h/day** at 2.5% **[inferred]** | Mods drown (Reddit AutoMod still needed **~41%** human share in 2021 TR) |
| **Security** | Model as PDP; CSAM via LLM = wrong control | Hash + 2258A report; dual-control reviewers; ZT MCP `record_decision` only | Regex bypass |
| **Scale / law** | Nothing accurate for DSA Art. 15(e) | Art. 15/17/20 templates **2026** cycle; omni scores as **signals**; WORM appeals | No nuance; still need hashes |

**Decision.** **B.** Hash-match **first** for known CSAM (PhotoDNA / CSAI / PDQ) + mandatory CyberTipline if US ESP. Keyword/sync like Discord AutoMod (**>45M** blocked before post). Dual independent classifiers; disagreement → human. LLM **async** for policy-as-prompt on ambiguous text (Discord AutoMod AI pattern: **alert**, not silent auto-ban). Omni-moderation **free** but rate-limit (bulkhead). Replace Perspective before **2026-12-31**. WORM + DSA appeals. **Do not** put CSAM bytes in LangSmith. Classifier 5xx → fail-closed on hash/CSAM; spam fail-open vs fail-closed is a product call I state.

#### Copilot delta (not a third scored scenario)

If the interviewer swaps to **IDE copilot**: keep Scenario 1’s gateway/IdP, **drop** hybrid search as the product, **add** 09 sandbox + 12 HITL + Rule of Two. Completions: Cheney proxy/H2/cancel, **mean < 200 ms**, peak **8k RPS**, issued QPS including ~50% typed-through, **do not** invent p99. Completions **policy** if they demand percentiles: **150 / 350 / 800 ms [inferred]** — not GitHub. Agentic: MXC/cloud sandbox (**2026-06-02**), `preToolUse` deny, **never** host shell. Budget **$10.08 / 1k** Luna vs **$100.80 / 1k** Terra 12-loop; uncapped 30-loop **$252 / 1k** **[inferred]**. Completions Luna **$0.16 / 1k**. PDP **then** pause. GitHub `--yolo` is a demo switch, not enterprise policy. Isolation ≠ authorization; creds outside the guest.

---

## Common Failure Modes

| They built… | On a problem that needed… | What breaks |
| --- | --- | --- |
| Chatbot **with no retrieval** | Private / changing corpus | Confident wrong SLA; EchoLeak-class if they later “just add Drive” |
| Search **with no ACL predicate** | Multi-user corpus | Cross-tenant leak; “the prompt said don’t show HR” is not a control |
| Copilot with **host shell** | IDE agent | Lethal trifecta complete; 09/13 ban `LocalShellBackend` |
| Moderation **LLM-only** | UGC at QPS | p99 and `$` explode; CSAM missed (hash is the control); DSA has nothing accurate to report |
| Completions latency on an **agent** board | Ghost-text | You quote 200 ms mean while the product is 12 tool loops |
| Skip-ANN stuffed 200k **Sonnet** at 50 QPS | High-QPS lookup | **$60 / 1k** corpus tokens vs RAG **~$10** |
| SC N=40 on L1 chat | Support widget | 16 **~$780 / 1k** Sonnet calls; never a chat default |
| Judge on the user path | Any interactive SLO | 16 extract p99 **12,000 ms**; 04/17 sidecar |
| Fat namespace + metadata ACL | 20M × 1536-d @ 50 QPS | 15 **~$263k/mo vs ~$8.3k/mo** |
| Perspective as 2027 SoT | UGC toxicity | Sunset **2026-12-31** |
| Safety as wrap-up boxes | Any archetype | Rails + ZT MCP were default; model treated as PDP |
| Typed-through completions without cancel | Ghost-text | Pay and add load for unused work (Cheney ~50%) |
| Eval poison / PAN in goldens | CI gate (17) | LangSmith indefinite retention; GDPR miss |
| HITL expire-**approve** | Copilot agent | Confused deputy; 12 expire-deny |
| Serving `latest` index | Search | Dual-run miss; silent recall death |

No public post-mortem corpus beyond vendor talks (Cheney, Discord AutoMod, EchoLeak advisory). Do not invent incidents.

---

## Key Takeaways

- This round is **composition of 01–17**, not Twitter HLD, not Google/Meta training-loop ML SD, not PagedAttention serving. Draw the **generic five-plane** product first; archetype deltas add/drop boxes. Guardrails + Zero-Trust MCP are **default HLD boxes**. **The model is never the PDP.** HITL is a pause (12).
- **Pull vs drop:** skip ANN **< ~200k tokens**; skip host shell; skip LLM-every-item; skip SC N=40 as chat default; skip GraphRAG unless “themes.” Chatbot: stream, checkpointer, Adaptive-RAG (01). Search: hybrid + ACL **predicate** (Microsoft permission trimming). Copilot: **name** completions vs agent; Cheney **>400M/day**, **~8k RPS**, **mean < 200 ms**, H2 cancel; 2026 MXC/cloud; Rule of Two; PDP then HITL. Moderation: hash → keyword → classifier → LLM queue → human; Discord **>45M**; Perspective sunset **2026-12-31**; omni **$0** as **signal**; DSA appeals.
- **`$ / 1k` [inferred]:** chat Luna **$7.04** / +RAG **$7.55** / cached **$4.88** / Sonnet **$96** / cached Sonnet **$63.60**. Search Terra+index **$9.87**; skip-ANN Luna **$4.48** vs Sonnet **$60**. Copilot 12-loop Terra **$100.80** / Luna **$10.08** / 30-loop **$252** / completions Luna **$0.16**. Moderation omni **$0** / Comprehend **$1.50** / Azure **$0.38** / LLM-every Sonnet **$4.65** / 2.5% cascade Luna **$0.0085**. Eval Luna-m3 **$2.32** / Sonnet-m3 **$21.13**; 100% shadow **+$880/mo**. RU cliff **~$263k vs ~$8.3k/mo** (15).
- Latency is **[inferred] policy** except Cheney **mean**. Chatbot TTFT **400 / 1,200 / 3,000 ms**; e2e **2,000 / 6,000 / 12,000**. Completions policy **150 / 350 / 800 ms** — **not** GitHub p99 80 ms (`> ⚠️ Gap`). Streaming **same `$`**, buys TTFT/cancel. Fast mode **99.9%** = throughput; Bedrock **99.5%** = capacity.
- Fallback: BM25-only / `retrieval_degraded`; last-good bundle; moderation fail-closed CSAM. Circuit-break **index independently of FM** and **classifier vs LLM queue**. Dual-run bundles (17). Zero-Trust MCP is **in this file**: OAuth 2.1, RFC 8707, no passthrough, hash-pin. Deny `include_values` / host shell. PII **before embed**. EchoLeak = RAG as untrusted input. Reviewer ≠ user ACL. Immutable WORM logs.

---

## Interview Q&A

**Q1. What is this round, in one minute?**  
I compose 01–17 into a user-facing product. Control owns IdP, PDP, bundle alias, hop cap, spend, HITL queue, MCP allowlist. Data owns rails, retrieve/tools/generate, stream. Persistence is checkpointer, index alias, WORM. Tool proxies are MCP behind a gateway — identity from the JWT. Telemetry is ACL-redacted traces, `$`, decision WORM, judge **sidecar**. This is not Twitter HLD, not a feature-store training loop, not PagedAttention. Guardrails and Zero-Trust MCP are default boxes. The model is never the PDP.

**Q2. Walk a request through your diagram.**  
Gateway binds tenant from the JWT, not body `user_id`. Flag SDK resolves a sticky bundle on `thread_id` (17). Input rails cut injection likelihood — they do not authorize tools. Orchestrator runs one graph. Tools hit the MCP PEP: hash-pin, RFC 8707, no passthrough, ns from JWT. Stream. Checkpoint if there is a thread (13: disconnect does not cancel). Log hashes, not bodies. I would not put the judge on this path.

**Q3. First 6 minutes I ask…**  
I ask NFR questions that **fork boxes**, not “who are the users?” Corpus size — under ~200k static tokens I **drop ANN**. Latency class — ghost-text vs “a few seconds” vs batch. Tenancy — namespace-per-tenant or RLS, never ACL in the prompt. Regulated data — PII before embed, residency, WORM. Online vs batch — dreaming and reindex are control-plane jobs. HITL — refund/shell/takedown means PDP then pause. Tools — sandbox + ZT MCP, never host shell. Modality — hash-first for known CSAM. Then I size QPS (10k DAU → **2.8 avg / 28 peak** on the stated envelope) with judge off p99.

**Q4. Four archetypes — what do you pull vs drop?**  
Chatbot: stream, checkpointer, Adaptive-RAG; drop uncapped ReAct and SC N=40. Search: hybrid + ACL predicate before ANN; drop ANN under ~200k; drop sequential BM25-then-dense. Copilot: I **name** completions vs agent; pull H2 cancel / MXC sandbox / Rule of Two; drop host shell and “model is policy.” Moderation: cascade hash→keyword→classifier→async LLM→human; drop LLM-every and Perspective as 2027 SoT.

**Q5. Give me `$ per 1k` for all four.**  
**[inferred]** from Luna **$0.20/$1.20**, Terra **$2/$12**, Sonnet **$3/$15**, cache 0.1×. Chat 8-turn 2k/400: Luna **$7.04**; +RAG 40% **$7.55**; cached **$4.88**; Sonnet **$96**; cached Sonnet **$63.60**. Search Terra+index **$9.87**; skip-ANN 200k Luna **$4.48** vs Sonnet **$60** — retrieval can win on `$` at high QPS. Copilot 12-loop Terra **$100.80** / Luna **$10.08** / 30-loop **$252**; completions Luna **$0.16**. Moderation omni **$0**; Comprehend **$1.50**; Azure **$0.38**; LLM-every Sonnet **$4.65**; 2.5% cascade Luna **$0.0085**. Eval Luna-m3 **$2.32**, Sonnet-m3 **$21.13**; 100% shadow **+$880/mo**. RU cliff **~$263k vs ~$8.3k/mo** (15). GPT-5.6 HTML was JS-gated (`> ⚠️ Gap`).

**Q6. What p50/p95/p99 do you actually quote?**  
Nobody publishes product percentiles (`> ⚠️ Gap`). I contract **[inferred] policy**: chatbot TTFT **400 / 1,200 / 3,000 ms**, e2e **2,000 / 6,000 / 12,000 ms**. Search retrieve **80–400 / 120 ms–1.5 s / 250 ms–3 s** then fail-closed; answer e2e **1,300 / 4,200 / 12,300 ms**. Copilot completions: Cheney **mean < 200 ms** only; if they demand an SLO I put **policy 150 / 350 / 800 ms** and I will **not** invent GitHub p99 80 ms. Agent loop **8 / 45 / 180 s**. Moderation hash **<10 / 20 / 50 ms**, classifier **20 / 40 / 100 ms**, LLM **queued 800 / 2,500 / 8,000 ms**. Flag SDK **1 / 10 / 50 ms**. Fast mode 99.9% is throughput; Bedrock 99.5% is capacity.

**Q7. Circuit breaker and fallback.**  
Independent breakers on **retrieve vs generate**, closed → open → half-open, and on **classifier vs LLM queue**. Index 5xx must not freeze the FM; FM 429 must not skip ACL retrieve. Fallback: last-good retrieve cache → BM25-only → `retrieval_degraded` refusal if ungrounded is forbidden. Flags: last-good bundle. Sandbox pool empty: **never** host exec. Moderation: fail-closed on CSAM/hash; spam is a product choice I state. Transient 429 retries with full jitter; poison dim mismatch / PAN golden is 4xx, no retry.

**Q8. Zero-Trust MCP — there is no module 19.**  
I put the PEP **here**, on every archetype. OAuth 2.1 + PKCE, RFC 8707 resource = this server, RFC 8693 **no** passthrough, hash-pin tools, identity from JWT. Chatbot: `retrieve_public_kb` / `retrieve_hr` / `create_ticket` HITL. Search: `query_index`, **deny `include_values`**. Copilot: `execute` in sandbox only, `preToolUse` deny, no host `bash`. Moderation: `enqueue_review` / `record_decision`, not `set_alias`. Assistants never `promote_tag`. MCP is not the PDP. Schema-valid JSON is not authz. CVE-2025-54136 if I skip re-hash; CVE-2025-6514 if I `open()` hostile auth metadata.

**Q9. Copilot — completions vs agent in 60 seconds.**  
Two products. Completions: copilot-proxy, H2 cancel typed-through (~half), colocate, **>400M/day**, peak **8k RPS**, **mean < 200 ms** — mean, not p99. Agentic: 2026 MXC local + cloud sandbox, hop cap, checkpointer for HITL, Rule of Two, PDP then pause. I would not quote 200 ms mean on a 12-loop board (**$100.80 / 1k** Terra **[inferred]**). `--yolo` is not policy.

**Q10. Moderation cascade vs LLM-every.**  
Hash-first known CSAM (PhotoDNA/CSAI/PDQ) + 2258A if US ESP. Keyword like Discord AutoMod (**>45M**). Dual classifiers; disagree → human. LLM **async** on mid-confidence; omni **$0** as a **signal** behind an RPM bulkhead. Perspective dies **2026-12-31**. DSA Art. 15/17/20 WORM + appeals. LLM-every Sonnet is **$4.65 / 1k** and p99 **8 s** on the post path — I would not ship it. Reviewer ACL ≠ user ACL; no CSAM in LangSmith.

**Q11. Two scored designs in 90 seconds.**  
B2B support: **B** hybrid + namespace-per-tenant + stream + hop cap. Skip ANN **per tenant** if <200k static. Luna **$7.04–$7.55 / 1k**; avoid 15 **$263k/mo** fat ns. Not stuffed Sonnet. Not uncapped agent. UGC: **B** cascade + dual-model + hash + appeal. Not LLM-every. Not keyword-only. If they swap to copilot, I keep the gateway and apply the **delta** — I do not restart a third scored scenario.

**Q12. PII, EchoLeak, and the wrap.**  
Detect → redact → audit **before embed**. Vec2Text **92% / 89%**. EchoLeak (CVE-2025-32711, 9.3) is retrieved untrusted input — RAG is Rule of Two [A]. I name one cost cliff and one wrong-archetype in the wrap. Dual-run `index_gen`. I do not add safety in minute 44.

---

## Key Numbers to Memorize

### Round / invariant / archetypes
| Number | What |
| --- | --- |
| **45–60 min** | Product AI system-design round; 0–6 clarify forks |
| **`(model_id, prompt_hash, schema_hash, decoding_params, index_gen, eval_suite_id)`** | Release pin (17); sticky `thread_id` |
| **< ~200k tokens (~500 pages)** | Skip ANN; Anthropic stuff + cache |
| **ACL predicate / permission trimming** | Microsoft semantic index; never ACL in the prompt |
| **HITL ≠ PDP** | 12 pause; model never policy |
| **Rule of Two** | Meta 2025-10-31: at most two of [A,B,C] |
| **>400M/day / ~8k RPS / mean < 200 ms** | Cheney Copilot completions — **mean**, not p99 |
| **~50% typed-through** | Cheney cancel; budget issued QPS |
| **2026-06-02** | GitHub MXC local + cloud sandbox public preview |
| **>45M** | Discord AutoMod blocked before post |
| **58.9% / 103.6M** | Reddit AutoMod share of 2021 removals |
| **2026-12-31** | Perspective sunset |
| **§ 2258A / DSA 2026 cycle** | CyberTipline; (EU) 2024/2835 templates |
| **CVE-2025-32711 / 9.3** | EchoLeak — RAG untrusted input |

### `$` **[inferred]** where marked
| Number | What |
| --- | --- |
| **$0.20 / $1.20 ; $2 / $12 ; $3 / $15** | Luna in/out; Terra in/out; Sonnet in/out per 1M |
| **1.25× / 0.1×** | Anthropic cache write / read (03) |
| **[inferred] $7.04 / $7.55 / $4.88** | Chat Luna 8-turn; +RAG 40%; cached |
| **[inferred] $96 / $63.60** | Chat Sonnet uncached / cached prefix |
| **~$780 / 1k** | 16 SC N=40 Sonnet **calls** — never chat default |
| **[inferred] $9.87 / $4.48 / $60** | Search Terra+index; skip-ANN Luna; skip-ANN Sonnet corpus |
| **[inferred] ~$263k/mo vs ~$8.3k/mo** | 15 Plan 1 fat ns vs tenant ns |
| **[inferred] $100.80 / $10.08 / $252 / $0.16** | Copilot 12-loop Terra / Luna / 30-loop / completions Luna |
| **[inferred] $0 / $1.50 / $0.38 / $4.65 / $0.0085** | Omni / Comprehend / Azure / LLM-every Sonnet / 2.5% cascade Luna |
| **[inferred] $2.32 / $21.13 / +$880/mo** | Luna-m3 / Sonnet-m3 / 100% shadow on 1M Luna |
| **> ⚠️ Gap** | GPT-5.6 HTML JS-gated; Azure calculator `$` gated |

### Latency / capacity / availability (numeric ms)
| Number | What |
| --- | --- |
| **400 / 1,200 / 3,000 ms** | **[inferred policy]** chatbot TTFT p50/p95/p99 |
| **2,000 / 6,000 / 12,000 ms** | **[inferred policy]** chatbot e2e |
| **80–400 / 120 ms–1.5 s / 250 ms–3 s** | **[inferred policy]** search retrieve+rerank |
| **1,300 / 4,200 / 12,300 ms** | **[inferred policy]** search answer e2e |
| **mean < 200 ms** | Cheney completions — **not** p99; **not** 80 ms |
| **150 / 350 / 800 ms** | **[inferred policy]** completions SLO if demanded |
| **8 / 45 / 180 s** | **[inferred policy]** copilot agent tool-loop |
| **<10 / 20 / 50 ms ; 20 / 40 / 100 ms** | Moderation hash ; classifier **[inferred]** |
| **800 / 2,500 / 8,000 ms** | LLM moderation slow-path **queued** |
| **1 / 10 / 50 ms** | Flag SDK (17) **[inferred]** |
| **200–500 ms** | 01 retrieve timeout **policy** |
| **2.8 → 28 QPS** | 10k DAU chatbot envelope **[inferred]** |
| **4,630 avg / 8k peak** | Copilot issued completions QPS (Cheney) |
| **11.6 QPS + 125 reviewer-hours** | 1M comments/day cascade **[inferred]** |
| **99.9% / 99.5%** | Fast mode **throughput** / Bedrock **capacity** |
| **RPO checkpoint / alias minutes / WORM** | Chat / search / moderation trade-offs |
| **RFC 8707 / RFC 8693** | MCP resource / **no** token passthrough |
| **detect → redact → audit** | **Before embed**; never log CSAM/PII |

**Dates:** research frozen **2026-09-03** (57 sources). Do not treat inferred `$` or ms as list prices or vendor SLOs. Do not invent GitHub Copilot p99 80 ms. Do not recopy 01/13/15/16 internals on the whiteboard — cite and compose.
