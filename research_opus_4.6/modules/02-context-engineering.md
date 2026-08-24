# Module 02: Context Engineering

**Scope**: Context assembly pipelines, prompt caching mechanics, semantic caching, context compression, multi-tenant isolation, prompt injection defense, tiered memory, durable execution, and production code patterns.
**Prerequisite**: Module 01 (LLM Foundations), familiarity with Python, REST APIs, Redis.
**Last updated**: 2026-08-21 | **Sources consulted**: 58

---

## 1. System Topology & Data Flow

### 1.1 Architecture Diagram

```
 ┌────────────────────────────────────────────────────────────────────────────────────┐
 │                              CONTROL PLANE                                         │
 │  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐  ┌────────────────────┐  │
 │  │  API Gateway   │  │ Context Router│  │ Budget Manager│  │  Tenant Registry   │  │
 │  │  - Auth/mTLS   │  │  - Model tier │  │  - Token caps │  │  - Isolation mode  │  │
 │  │  - Rate limit  │  │  - Complexity │  │  - Cost ceil  │  │  - Silo/Pool/Brdg  │  │
 │  │  - PII filter  │  │  - Cascading  │  │  - Per-tenant │  │  - Feature flags   │  │
 │  └──────┬────────┘  └──────┬────────┘  └──────┬────────┘  └────────┬───────────┘  │
 │         │                  │                   │                    │               │
 └─────────┼──────────────────┼───────────────────┼────────────────────┼───────────────┘
           │                  │                   │                    │
 ┌─────────┼──────────────────┼───────────────────┼────────────────────┼───────────────┐
 │         ▼                  ▼                   ▼                    ▼               │
 │  ┌─────────────────────────────────────────────────────────────────────────────┐   │
 │  │                     CONTEXT ASSEMBLY ENGINE                                 │   │
 │  │                                                                             │   │
 │  │  ┌─────────────────────────────────────────────────────────────────────┐    │   │
 │  │  │  LAYER 1: System Context (Instruction Layer)                       │    │   │
 │  │  │  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐               │    │   │
 │  │  │  │ Role/Identity│ │ Constraints  │ │ Output Format│               │    │   │
 │  │  │  │ <background> │ │ <rules>      │ │ <format>     │               │    │   │
 │  │  │  └──────────────┘ └──────────────┘ └──────────────┘               │    │   │
 │  │  └────────────────────────────┬────────────────────────────────────────┘    │   │
 │  │                               ▼                                            │   │
 │  │  ┌─────────────────────────────────────────────────────────────────────┐    │   │
 │  │  │  LAYER 2: Tool Definitions (MCP + Native)                          │    │   │
 │  │  │  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐               │    │   │
 │  │  │  │ MCP Servers  │ │ Native Tools │ │ Tool Schemas │               │    │   │
 │  │  │  │ - Resources  │ │ - Functions  │ │ - JSON Schema│               │    │   │
 │  │  │  │ - Prompts    │ │ - Actions    │ │ - Validation │               │    │   │
 │  │  │  └──────────────┘ └──────────────┘ └──────────────┘               │    │   │
 │  │  └────────────────────────────┬────────────────────────────────────────┘    │   │
 │  │                               ▼                                            │   │
 │  │  ┌─────────────────────────────────────────────────────────────────────┐    │   │
 │  │  │  LAYER 3: Retrieved Context (RAG + Persistent Memory)              │    │   │
 │  │  │  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐               │    │   │
 │  │  │  │ Vector DB    │ │ SQL/Graph DB │ │ Agent Memory │               │    │   │
 │  │  │  │ - Semantic   │ │ - Structured │ │ - Episodic   │               │    │   │
 │  │  │  │ - k-NN       │ │ - Exact      │ │ - Semantic   │               │    │   │
 │  │  │  │ - Reranked   │ │ - Fresh      │ │ - Procedural │               │    │   │
 │  │  │  └──────────────┘ └──────────────┘ └──────────────┘               │    │   │
 │  │  └────────────────────────────┬────────────────────────────────────────┘    │   │
 │  │                               ▼                                            │   │
 │  │  ┌─────────────────────────────────────────────────────────────────────┐    │   │
 │  │  │  LAYER 4: Ephemeral Context (Conversation + Runtime)               │    │   │
 │  │  │  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐               │    │   │
 │  │  │  │ Summarized   │ │ Sliding Win  │ │ Current Turn │               │    │   │
 │  │  │  │ History      │ │ (last N raw) │ │ + Tool Reslt │               │    │   │
 │  │  │  └──────────────┘ └──────────────┘ └──────────────┘               │    │   │
 │  │  └─────────────────────────────────────────────────────────────────────┘    │   │
 │  │                                                                             │   │
 │  │  ┌───────────────────────────────────────────────────┐                      │   │
 │  │  │  TOKEN BUDGET CONTROLLER                          │                      │   │
 │  │  │  - Priority-based allocation per layer            │                      │   │
 │  │  │  - Truncation (middle-out) as backstop            │                      │   │
 │  │  │  - 10-15% safety margin for non-English text      │                      │   │
 │  │  └──────────────────────┬────────────────────────────┘                      │   │
 │  │                         │                                                    │   │
 │  └─────────────────────────┼────────────────────────────────────────────────────┘   │
 │                            │                                                        │
 │  ┌─────────────────────────▼────────────────────────────────────────────────────┐   │
 │  │                     HIERARCHICAL CACHE LAYER                  DATA PLANE     │   │
 │  │                                                                              │   │
 │  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐    │   │
 │  │  │  L0: Semantic │  │  L1: Prefix  │  │  L2: History │  │  L3: Full    │    │   │
 │  │  │  Cache        │  │  Cache       │  │  Cache       │  │  Inference   │    │   │
 │  │  │  (embed sim)  │  │  (sys+tools) │  │  (conv hist) │  │  (no cache)  │    │   │
 │  │  │  100% bypass  │  │  50-90% save │  │  50-90% save │  │  0% save     │    │   │
 │  │  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘    │   │
 │  │         │ hit?            │ hit?             │ hit?            │             │   │
 │  │         └────────►no──────┴────────►no───────┴────────►no─────┘             │   │
 │  │                                                                              │   │
 │  └──────────────────────────────────────────────────────────────────────────────┘   │
 │                            │                                                        │
 │  ┌─────────────────────────▼────────────────────────────────────────────────────┐   │
 │  │                     INFERENCE ENGINE                                         │   │
 │  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐    │   │
 │  │  │ LLM Provider │  │ Self-Hosted  │  │ Fallback     │  │ Response     │    │   │
 │  │  │ (API)        │  │ (vLLM/SGLng) │  │ Provider     │  │ Validator    │    │   │
 │  │  │ Claude/GPT   │  │ Llama/DS     │  │ - Retry      │  │ - Schema chk │    │   │
 │  │  │ Gemini       │  │              │  │ - Circuit brk│  │ - PII scrub  │    │   │
 │  │  └──────────────┘  └──────────────┘  └──────────────┘  └──────────────┘    │   │
 │  └──────────────────────────────────────────────────────────────────────────────┘   │
 │                            │                                                        │
 │  ┌─────────────────────────▼────────────────────────────────────────────────────┐   │
 │  │                     MCP TOOL PROXY LAYER                                     │   │
 │  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐    │   │
 │  │  │ MCP Client   │  │ Action Screen│  │  Sandbox     │  │ Result       │    │   │
 │  │  │ - stdio      │  │ - Intent chk │  │  - gVisor    │  │ Injector     │    │   │
 │  │  │ - HTTP/SSE   │  │ - RBAC       │  │  - Timeout   │  │ - Sanitize   │    │   │
 │  │  │ - Discovery  │  │ - Dual-LLM   │  │  - Resource  │  │ - Truncate   │    │   │
 │  │  └──────────────┘  └──────────────┘  └──────────────┘  └──────────────┘    │   │
 │  └──────────────────────────────────────────────────────────────────────────────┘   │
 │                                                                                     │
 └─────────────────────────────────────────────────────────────────────────────────────┘

 ┌─────────────────────────────────────────────────────────────────────────────────────┐
 │                         PERSISTENCE LAYER                                           │
 │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐   │
 │  │ Redis 8.6    │  │ Vector DB    │  │ PostgreSQL   │  │ Object Store (S3)    │   │
 │  │ - Hot memory │  │ - Pinecone   │  │ - Checkpoints│  │ - WORM audit logs    │   │
 │  │ - Sem. cache │  │ - Weaviate   │  │ - Tenant cfg │  │ - Cold conversation  │   │
 │  │ - Session    │  │ - Milvus     │  │ - Event log  │  │ - 7yr retention      │   │
 │  └──────────────┘  └──────────────┘  └──────────────┘  └──────────────────────┘   │
 └─────────────────────────────────────────────────────────────────────────────────────┘

 ┌─────────────────────────────────────────────────────────────────────────────────────┐
 │                         TELEMETRY & OBSERVABILITY                                   │
 │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐   │
 │  │ OpenTelemetry│  │ Context Audit│  │ Cost Tracker │  │ Quality Evaluator    │   │
 │  │ - Traces     │  │ - Mutations  │  │ - Cache hits │  │ - Context rot detect │   │
 │  │ - Metrics    │  │ - Injection  │  │ - Per-tenant │  │ - Hallucination      │   │
 │  │ - p50/p95/p99│  │ - PII events │  │ - Budget burn│  │ - Positional bias    │   │
 │  └──────────────┘  └──────────────┘  └──────────────┘  └──────────────────────┘   │
 └─────────────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 Request-Flow Narrative

A client request enters the **API Gateway**, which authenticates via mTLS or API key, enforces per-tenant rate limits, and runs PII detection (OpenAI Privacy Filter or Presidio). Detected PII is redacted or tokenized before any downstream processing. The gateway attaches a correlation ID and tenant ID to the request envelope.

The **Context Router** classifies the request by complexity. Simple queries route to cheaper models (DeepSeek V4 Flash at $0.14/M); complex reasoning routes to frontier models (Claude Opus 5 at $5/M input). Cascading strategies answer with the small model first and escalate only if a confidence check fails -- achieving 97% of GPT-4 quality at 24% of cost (MixLLM benchmark).

The **Context Assembly Engine** builds the prompt in four layers, executed top-to-bottom:

**Layer 1 (System Context)** loads the instruction set: role identity, behavioral constraints, output format, and edge-case handling. Content is structured using XML tags for Claude (`<instructions>`, `<rules>`, `<format>`) or Markdown headers for GPT models (`## General Instructions`, `# Tools`). This layer is static across requests and placed first to maximize prefix cache hits.

**Layer 2 (Tool Definitions)** appends MCP server schemas and native tool definitions. MCP provides three primitives -- tools (actions), resources (read-only context), and prompts (reusable templates) -- over stdio (local) or HTTP/SSE (remote) transports. Tool schemas must remain byte-identical and in the same order across requests; any change invalidates the prefix cache for everything downstream.

**Layer 3 (Retrieved Context)** performs just-in-time retrieval. The engine maintains lightweight identifiers (file paths, queries, URLs) rather than preloading entire documents. At runtime, it issues tool calls to vector DBs, SQL databases, or MCP resources to fetch precisely what the current query needs. Retrieved chunks are reranked by relevance and priority-ordered. Agent memory (episodic, semantic, procedural) is loaded from Redis or the vector store, scoped to the current tenant and session.

**Layer 4 (Ephemeral Context)** appends the conversation. Older turns are summarized into a living summary that is rewritten as the conversation evolves. The last N raw turns are kept verbatim in a sliding window. Tool call results from the current turn are appended last, immediately before the user's current message.

The **Token Budget Controller** enforces allocation percentages per layer (e.g., 15% system, 10% tools, 30% retrieval, 45% conversation). If any layer exceeds its budget, the controller applies truncation -- dropping from the middle first to exploit the U-shaped attention curve. A 10-15% safety margin is reserved for tokenizer discrepancies, especially on non-English text.

The assembled prompt hits the **Hierarchical Cache Layer**. L0 (semantic cache) checks whether an embedding-similar query was recently answered -- if the cosine similarity exceeds the threshold (typically 0.95), the cached response is returned directly, bypassing the LLM entirely. On L0 miss, L1 (prefix cache) checks whether the system prompt + tool definitions prefix is cached at the provider -- yielding 50-90% input cost savings on cache hits. L2 extends prefix caching to include conversation history. On full miss, the request proceeds to L3 (full inference).

The **Inference Engine** sends the assembled context to the selected provider. On tool-call responses, the **MCP Tool Proxy Layer** intercepts: the Action Screener validates intent alignment (dual-LLM pattern separates planning from execution), the Sandbox executes with resource limits, and the Result Injector sanitizes and truncates output before reinjecting into context for the next generation step.

Throughout the pipeline, the **Telemetry layer** records context assembly latency, cache hit rates, token counts, cost per request, and PII detection events. Context mutations are logged to an immutable audit trail for compliance (WORM storage, 7-year retention for regulated workloads).

### 1.3 Prompt Engineering Patterns (2026)

```
 ┌─────────────────────────────────────────────────────────────────┐
 │                    PROMPT CONSTRUCTION                          │
 │                                                                 │
 │  ┌─────────────────────────────────────────────────────────┐   │
 │  │  1. Role Identity (specific, not generic)               │   │
 │  │     "You are a tax preparation assistant for US         │   │
 │  │      individual filers using Form 1040"                 │   │
 │  │     NOT: "You are a helpful assistant"                  │   │
 │  └───────────────────────┬─────────────────────────────────┘   │
 │                          ▼                                      │
 │  ┌─────────────────────────────────────────────────────────┐   │
 │  │  2. Rules & Constraints                                 │   │
 │  │     - Calm, direct language (no "CRITICAL!", "NEVER!")  │   │
 │  │     - XML tags for Claude: <rules>, <constraints>       │   │
 │  │     - Markdown for GPT: ## Rules                        │   │
 │  └───────────────────────┬─────────────────────────────────┘   │
 │                          ▼                                      │
 │  ┌─────────────────────────────────────────────────────────┐   │
 │  │  3. Few-Shot Examples (wrapped in <example> tags)       │   │
 │  │     - Diverse, canonical (not exhaustive edge cases)    │   │
 │  │     - Monitor for drift against current data dist.      │   │
 │  └───────────────────────┬─────────────────────────────────┘   │
 │                          ▼                                      │
 │  ┌─────────────────────────────────────────────────────────┐   │
 │  │  4. CoT / Extended Thinking                             │   │
 │  │     - Built into reasoning modes (GPT-5, Claude, etc.) │   │
 │  │     - Decide WHEN to spend reasoning tokens             │   │
 │  │     - +61% accuracy over zero-shot baselines            │   │
 │  └───────────────────────┬─────────────────────────────────┘   │
 │                          ▼                                      │
 │  ┌─────────────────────────────────────────────────────────┐   │
 │  │  5. Edge Cases & Output Format                          │   │
 │  │     - Explicit handling for ambiguous inputs            │   │
 │  │     - JSON schema, structured output, delimiters        │   │
 │  └─────────────────────────────────────────────────────────┘   │
 │                                                                 │
 └─────────────────────────────────────────────────────────────────┘
```

**Anti-pattern to internalize**: Aggressive language ("CRITICAL!", "YOU MUST", "NEVER EVER") actively degrades output quality on newer Claude models. Calm, direct instructions outperform emphatic ones.

### 1.4 MCP Protocol Integration

```
 ┌──────────────────────────────────────────────────┐
 │                  MCP HOST                        │
 │  (Claude Desktop, IDE, Agent Runtime)            │
 │                                                  │
 │  ┌──────────────────────────────────────────┐   │
 │  │              MCP CLIENT                   │   │
 │  │  - Manages N server connections           │   │
 │  │  - Routes tool calls to correct server    │   │
 │  │  - Aggregates schemas into context        │   │
 │  └────┬─────────────┬─────────────┬─────────┘   │
 │       │ stdio       │ stdio       │ HTTP/SSE     │
 └───────┼─────────────┼─────────────┼──────────────┘
         ▼             ▼             ▼
 ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
 │ MCP Server A │ │ MCP Server B │ │ MCP Server C │
 │ (Local)      │ │ (Local)      │ │ (Remote)     │
 │              │ │              │ │              │
 │ Tools:       │ │ Tools:       │ │ Tools:       │
 │  - read_file │ │  - query_db  │ │  - search    │
 │  - write_file│ │  - insert    │ │  - fetch_url │
 │              │ │              │ │              │
 │ Resources:   │ │ Resources:   │ │ Resources:   │
 │  - file://   │ │  - db://     │ │  - http://   │
 │              │ │              │ │              │
 │ Prompts:     │ │ Prompts:     │ │ Prompts:     │
 │  - summarize │ │  - analyze   │ │  - classify  │
 └──────────────┘ └──────────────┘ └──────────────┘
```

**Adoption scale (August 2026)**: 97M monthly SDK downloads (Python + TypeScript), 10,000+ public servers, adopted by Anthropic, OpenAI, Google, Microsoft, Amazon. Donated to the Agentic AI Foundation (Linux Foundation) in December 2025.

**Context overhead reduction**: Code execution with MCP reduces context overhead by up to 98.7% -- the agent sends code to execute rather than embedding entire datasets or tool outputs in the context window.

---

## 2. Core Mechanics & Algorithms

### 2.1 Context Assembly Priority Stacking

The context window is a capped per-turn budget. The assembly algorithm processes layers in priority order, enforcing allocation limits:

```
Assembly Algorithm:
  1. Reserve system_budget = window_size * 0.15
  2. Reserve tool_budget   = window_size * 0.10
  3. Reserve retrieval_budget = window_size * 0.30
  4. Reserve conversation_budget = window_size * 0.45
     (minus 10-15% safety margin for tokenizer variance)

  For each layer in priority order:
    tokens = tokenize(layer_content)
    if tokens > layer_budget:
      apply truncation_strategy(layer_content, layer_budget)
    append to assembled_context

  Final backstop: if total > window_size * 0.85:
    truncate from middle (positions 5-15 in 20-item ordering)
```

**Assembly ordering for maximum cache hits**:
1. System prompt (never changes) -- placed first
2. Tool definitions (changes rarely) -- must be byte-identical and in same order
3. Retrieved context (changes per query) -- priority-ordered by relevance
4. Summarized older conversation
5. Sliding window of last N raw turns
6. Current user message -- placed last

**Truncation strategies**:
- **Drop oldest**: Keep most recent N messages/tokens. Best when recency correlates with relevance.
- **Drop least relevant**: Score items by `recency_weight * recency + retrieval_score + user_action_weight`, drop lowest. Best for mixed feeds.
- **Drop from middle**: Exploits the U-shaped attention curve (models attend strongly to beginning and end). Default backstop strategy.
- **Truncation's virtue**: Nothing remaining is paraphrased -- preserves exact quotes, terminology, and numbers where summarization risks drift.

### 2.2 Prompt Caching Mechanics

#### Anthropic (Claude)

Anthropic caches KV-cache state at explicitly marked breakpoints using `cache_control` annotations. The cache key is a cryptographic hash of the exact bytes of the request prefix.

```
Cache Key Computation:
  key = hash(tools_bytes ++ system_bytes ++ messages_bytes_up_to_breakpoint)

  Invalidation rule: Changing ANY byte at or before breakpoint N
                     invalidates all cached state after N.
```

**Parameters**:
- Max 4 cache breakpoints per request, 20-block lookback per breakpoint
- 5-minute TTL (default): write at 1.25x base input, read at 0.10x (90% discount)
- 1-hour TTL: write at 2.0x base input, read at 0.10x
- Every successful read resets the TTL clock
- Minimum tokens per checkpoint: 512 (Opus 5/Fable 5) to 4,096 (Opus 4.5-4.6, Haiku 4.5)
- Cache hierarchy order: tools -> system -> messages
- **Ordering constraint**: 1-hour TTL entries must precede 5-minute entries; violating this breaks caching silently

**Break-even math (5-minute TTL)**:
```
  1 write + 0 reads: 1.25x  (25% premium -- worse than uncached)
  1 write + 1 read:  avg 0.675x per call  (32.5% savings)
  1 write + 2 reads: avg 0.483x per call  (51.7% savings)
  Asymptotic limit:  0.10x per call       (90% savings)
```

**Pre-warming trick**: Send `max_tokens: 0` to warm the cache without generating output -- zero output token charges.

#### OpenAI (GPT)

OpenAI caching is automatic on pre-GPT-5.6 models -- zero code changes required. The system routes requests to servers that recently processed the same prefix.

```
  Pre-GPT-5.6: Free writes, 50% read discount, 1,024-token min prefix
  GPT-5.6+:    Requires prompt_cache_key, 1.25x writes, ~90% read discount
  Duration:    5-10 min (active hours), up to 1 hour (off-peak)
  GPT-5.5+:    24-hour extended cache available
```

**Stacking**: OpenAI Batch API gives 50% off; combined with prompt caching, this yields 75% total input cost reduction.

#### Google Gemini

Two modes serve different access patterns:

```
  Implicit (automatic): No code changes, ~24h TTL, free storage, auto discount
  Explicit (opt-in):    2,048 min tokens, 1h default TTL, $4.50/M tokens/hour storage

  Storage cost warning: 1M-token explicit cache for 24h = ~$108 in storage alone.
  Use implicit for low-reuse patterns.
```

### 2.3 Semantic Caching Algorithms

Semantic caching stores meaning rather than exact text, using embedding similarity to match semantically equivalent queries.

```
Semantic Cache Lookup:
  1. Embed incoming query:  q_emb = embed(query)
  2. Search vector index:   candidates = ann_search(q_emb, top_k=5)
  3. Filter by threshold:   hits = [c for c in candidates if sim(c, q_emb) > 0.95]
  4. If hits:               return cached_response(hits[0])
  5. Else:                  proceed to LLM, cache result keyed by q_emb

Eviction Policies (GPTCache):
  - LRU (Least Recently Used):   best general-purpose
  - LFU (Least Frequently Used): best for skewed popularity distributions
  - FIFO:                         simplest, adequate for uniform access
  - Random:                       baseline comparator
```

**Production hit rates by workload**:
- Customer support / analytics: 30-50%
- Conversational agents: 10-25%
- Template-heavy agent inner loops: 40-70%

**Invalidation challenge**: In an exact-match cache, invalidation is a key lookup. In a semantic cache, invalidation requires embedding the invalidation query, running ANN search (which may have recall errors), and removing matches. Practical approach: maintain a side index mapping semantic topics/document IDs to cache entries, classified at store time by the LLM itself.

**When NOT to use semantic caching**:
- Creative generation (temperature >0.5): 95%+ miss rates
- Stateful multi-turn conversations: context changes every message
- Personalized recommendations: structurally similar but semantically distinct

### 2.4 Context Compression

Five compression patterns can cut token costs 30-70%:

**LLMLingua (Microsoft Research)**:
```
  LLMLingua-1:  20x compression ratio, minimal performance loss
  LLMLingua-2:  Uses XLM-RoBERTa for token classification
                3-6x faster inference than LLMLingua-1
                95-98% accuracy retention
```

**Production compression pipeline**:
```
  Raw context
    │
    ▼
  Deduplication (exact + near-duplicate removal)
    │
    ▼
  Key extraction (entities, facts, numbers)
    │
    ▼
  Selective summarization (cheap LLM call, e.g., Haiku tier)
    │
    ▼
  Compressed context (62% fewer tokens)

  Overhead: ~200ms latency + one Haiku-tier LLM call
  Savings:  ~$2,100/month on moderate workloads
```

**Dynamic summarization for agents**: Keep last N turns in full plus a living summary of everything older, rewritten repeatedly as conversation evolves. This is the approach used by Claude Code's auto-compaction (fires at ~98% of effective window).

**Compression target priority**: RAG pipelines are the highest-yield target -- retrievals are reliably redundant, compression ratios are highest, and quality impact is lowest.

### 2.5 Token Counting and Budget Management

**The core problem**: Client-side token estimation diverges from API-side counting in production.

```
Known Discrepancies:
  - Tool calls:        Significant gap between tiktoken and OpenAI API response
  - Embeddings API:    ~9.5% discrepancy causing unexpected rate limit errors
  - Cross-provider:    MiniMax tokenizer counts 10-20% more than GPT-4o tiktoken
  - Non-English text:  Japanese, Arabic produce significantly larger tokens/char
  - Encoding mismatch: cl100k_base (GPT-3.5/4) vs o200k_base (newer, 200K vocab)
  - char/4 heuristic:  Common shortcut, frequent root cause of budget overruns
```

**Mitigation algorithm**:
```
  1. Cache the tokenizer encoder globally (avoid re-initialization overhead)
  2. Run 5-sample verification against provider API after setup
  3. Use provider's actual tokenizer when available
  4. Fall back to conservative multiplier (1.15x) for unknown tokenizers
  5. Maintain 10-15% safety margin for non-English text
  6. Log actual vs. estimated counts per request for drift monitoring
```

### 2.6 "Lost in the Middle" Phenomenon and Positional Bias

**The effect**: Performance follows a U-shaped curve -- models attend strongly to the beginning and end of context, poorly to everything in the middle.

```
Attention Strength vs. Position (20-document QA):

  High │ ██                                                    ██
       │ ██ ██                                              ██ ██
       │ ██ ██ ██                                        ██ ██ ██
       │ ██ ██ ██ ██                                  ██ ██ ██ ██
  Med  │ ██ ██ ██ ██ ██                            ██ ██ ██ ██ ██
       │ ██ ██ ██ ██ ██ ██                      ██ ██ ██ ██ ██ ██
       │ ██ ██ ██ ██ ██ ██ ██                ██ ██ ██ ██ ██ ██ ██
  Low  │ ██ ██ ██ ██ ██ ██ ██ ██ ██ ██ ██ ██ ██ ██ ██ ██ ██ ██ ██
       └───────────────────────────────────────────────────────────
         1  2  3  4  5  6  7  8  9  10 11 12 13 14 15 16 17 18 19 20
                              Document Position

  Accuracy drop: >30% for positions 5-15 vs. position 1 or 20
  GPT-4o (NoLiMa benchmark): 99.3% baseline -> 69.7% at 32K tokens
```

**Architectural cause (MIT 2025)**: Causal masking means Token #1 is visible to all subsequent tokens, accumulating disproportionate attention weight. Rotary Position Embedding (RoPE) introduces long-term decay that de-emphasizes middle content. This bias persists regardless of document order randomization.

**Practical mitigation**: Place the most critical information at the beginning and end of the context window. Use middle positions for lower-priority supporting material. Multi-scale Positional Encoding (Ms-PoE) can reduce bias without retraining, but no production model has fully eliminated position bias as of 2026.

### 2.7 Context Rot

Distinct from context overflow. Context rot is measurable output quality degradation as tokens increase, beginning well before the token limit.

**Chroma 2025 evaluation**: 18 frontier models (GPT-4.1, Claude 4, Gemini 2.5, Qwen3) -- every single model exhibited degradation as input length increased, even on simple tasks, even when far from full context.

Three compounding mechanisms:
1. **Lost-in-the-middle** effect (30%+ accuracy drops)
2. **Attention dilution** (100K tokens = 10 billion pairwise relationships)
3. **Distractor interference** (semantically similar but irrelevant content actively misleads)

Context rot produces no exceptions, no error codes -- subtly wrong answers that pass basic output validation. Nearly 65% of enterprise AI failures in 2025 were attributed to context drift or memory loss during multi-step reasoning.

---

## 3. Token Economics & NFR Analysis

### 3.1 Cost Formulas: Context Size Impact

**Base cost per 1K runs** (100K-token context, single turn, 500-token output):

```
Provider/Model          Input $/M    Output $/M    Cost per 1K runs
────────────────────────────────────────────────────────────────────
DeepSeek V4 Flash       $0.14        $0.28         $ 14.14
DeepSeek V4 Pro         $0.435       $0.87         $ 44.00
GPT-4.1                 $2.00        $8.00         $204.00
Claude Opus 5           $5.00        $10.00        $505.00
Claude Fable 5          $5.00        $10.00        $505.00
GPT-5.5                 $2.00*       $10.00        $205.00
Gemini 3.1 Pro          $4.00        $8.00         $404.00
────────────────────────────────────────────────────────────────────
Formula: (input_tokens/1M * input_price + output_tokens/1M * output_price) * 1000

* GPT-5.5: 2x surcharge above 272K tokens (cost doubles for >272K context)
  Gemini 2.5 Pro: 2x surcharge above 200K tokens
```

**Context scaling impact** -- cost per 1K runs at varying context sizes (Claude Opus 5):

```
Context Size    Input Cost/1K    Output Cost/1K    Total/1K    vs. 10K baseline
──────────────────────────────────────────────────────────────────────────────
10K tokens      $  50.00         $  5.00           $  55.00    1.0x
50K tokens      $ 250.00         $  5.00           $ 255.00    4.6x
100K tokens     $ 500.00         $  5.00           $ 505.00    9.2x
500K tokens     $2,500.00        $  5.00           $2,505.00   45.5x
1M tokens       $5,000.00        $  5.00           $5,005.00   91.0x
──────────────────────────────────────────────────────────────────────────────
Assumption: 500-token output per run, no caching
```

### 3.2 Prompt Caching Savings Across Providers

**Scenario**: 100K-token system prompt + tools, 10K-token user context, 1K runs in a 5-minute window.

```
                        Anthropic         OpenAI            Gemini
                        (5-min TTL)       (pre-GPT-5.6)    (Explicit)
──────────────────────────────────────────────────────────────────────
Naive cost (1K runs)    $500.00           $200.00           $400.00
Cache write (1x)        $  6.25 (1.25x)   $  0.00 (free)   $ 0.50
Cache reads (999x)      $ 49.95 (0.10x)   $ 99.90 (0.50x)  $ 19.98 (0.10x)
User context (1K)       $ 50.00           $ 20.00           $ 40.00
Storage cost            $  0.00           $  0.00           $ 0.45/hr
──────────────────────────────────────────────────────────────────────
Total with caching      $106.20           $119.90           $ 60.93*
Savings                 78.8%             40.1%             84.8%
──────────────────────────────────────────────────────────────────────
Assumptions: Anthropic=Claude Opus 5 ($5/M), OpenAI=GPT-4.1 ($2/M),
Gemini=Gemini 3.1 Pro ($4/M input). *Gemini storage for 1 hour.

Anthropic 1-hour TTL break-even: needs >= 3 reads to beat 5-min TTL.
OpenAI Batch API stacking: 50% batch + 50% cache = 75% total savings.
```

### 3.3 Semantic Caching ROI

```
Scenario: 10K queries/day, $0.50 avg cost/query, no caching baseline

                    Hit Rate    Queries Bypassed    Daily Savings    Monthly Savings
──────────────────────────────────────────────────────────────────────────────────────
Support/Analytics   40%         4,000               $2,000           $60,000
Conversational      15%         1,500               $  750           $22,500
Agent Inner Loops   55%         5,500               $2,750           $82,500
──────────────────────────────────────────────────────────────────────────────────────
Infrastructure cost: Redis + embedding model ~$500-2,000/month
One B2B SaaS team: 38% OpenAI bill reduction over a single weekend.
```

### 3.4 Context Compression Cost-Quality Trade-offs

```
Method              Compression    Accuracy     Latency      Cost/1K runs
                    Ratio          Retention    Overhead     (100K -> compressed)
──────────────────────────────────────────────────────────────────────────
No compression      1x             100%         0ms          $505.00 (Opus 5)
Dedup + extraction  2x             99%          50ms         $255.00
LLMLingua-2         5x             97%          100ms        $106.00
Full pipeline       3x             96%          200ms        $173.00
  (dedup+extract
   +summarize)
LLMLingua-1         20x            93%          300ms        $ 30.25
──────────────────────────────────────────────────────────────────────────
The sweet spot for most workloads: LLMLingua-2 at 5x compression.
The 20x ratio from LLMLingua-1 trades ~7% accuracy for ~17x cost reduction.
```

### 3.5 Latency SLA Targets

```
Operation                    p50        p95        p99        Notes
────────────────────────────────────────────────────────────────────
Context assembly             15ms       40ms       80ms       4-layer stack
Semantic cache lookup        5ms        15ms       30ms       ANN search
Prefix cache check           <1ms       <1ms       2ms        Provider-side
Token budget validation      2ms        5ms        10ms       Local compute
PII detection + redaction    20ms       50ms       100ms      Model-based
Context compression          100ms      200ms      400ms      LLMLingua-2
────────────────────────────────────────────────────────────────────
Total assembly (no compress) 25ms       60ms       120ms      Target: <100ms p95
Total assembly (w/ compress) 125ms      260ms      520ms      Compression adds ~200ms
────────────────────────────────────────────────────────────────────
LLM inference (TTFT)         200ms      800ms      2000ms     Provider-dependent
LLM inference (TPS)          50-100     30-60      15-30      Tokens/sec decode
────────────────────────────────────────────────────────────────────
End-to-end p95 target: <1 second for context assembly + TTFT
```

### 3.6 Non-Functional Requirements

```
NFR                 Target                  Rationale
────────────────────────────────────────────────────────────────────────────
Availability        99.9% (8.76h/yr)        Multi-provider fallback
RPO                 0 (session state)       Redis AOF + replication
RTO                 <30s (warm failover)    Pre-warmed standby instances
────────────────────────────────────────────────────────────────────────────
Cached PII          Redact BEFORE caching   GDPR: data leaving perimeter
  retention                                 is a processing boundary cross
Right to deletion   Must purge from all     Semantic cache: embed + ANN
                    cache tiers within 72h  search to find and remove
PII in prompts      39.7% carry sensitive   Redact at every outbound call,
                    data (2026 survey)      not just the first user message
────────────────────────────────────────────────────────────────────────────
Audit trail         WORM, 7-year retention  SOC2/HIPAA regulated workloads
Tenant isolation    Silo for enterprise,    KV-cache side-channel attacks
                    Pool+filter for SMB     can reconstruct prompts
Context reuse       NEVER across tenants    Equivalent to session fixation
────────────────────────────────────────────────────────────────────────────
Provider retention  OpenAI: 30 days         Zero-retention requires
                    Anthropic: 7 days       negotiated enterprise agreement
────────────────────────────────────────────────────────────────────────────
EU AI Act           Deployer obligations    Full effect August 2, 2026
                    take effect
GDPR Art. 4(5)      Tokenization is         Data remains personal data
                    pseudonymization        and stays in GDPR scope
```

---

## 4. Distributed Resilience & Security

### 4.1 Durable Execution

**The statelessness problem**: LLM inference is fundamentally stateless -- each request produces output with no retention. Every token in the context window costs on every call. For long-running agents (>4 hours), systems without state persistence have 90% higher risk of total task failure due to API timeouts.

**Three-tier memory architecture**:

```
 ┌─────────────────────────────────────────────────────────────┐
 │                      HOT TIER                               │
 │  Location:  Context window (in-prompt)                      │
 │  Latency:   N/A (most expensive per-token)                  │
 │  Cost:      $0.10-$10 per M tokens per call                 │
 │  Purpose:   Current reasoning, active tool results          │
 │  Capacity:  1M tokens max (effective: 600-700K)             │
 └──────────────────────────┬──────────────────────────────────┘
                            │ overflow / compaction
 ┌──────────────────────────▼──────────────────────────────────┐
 │                      WARM TIER                              │
 │  Location:  Redis 8.6, DynamoDB                             │
 │  Latency:   <1ms read/write                                 │
 │  Cost:      Pennies per GB-month                            │
 │  Purpose:   User preferences, session state, frequent facts │
 │  Features:  Semantic caching (73% token reduction),         │
 │             vector search, JSON documents, streams          │
 └──────────────────────────┬──────────────────────────────────┘
                            │ cold path / archival
 ┌──────────────────────────▼──────────────────────────────────┐
 │                      COLD TIER                              │
 │  Location:  Pinecone, Weaviate, Milvus, S3                 │
 │  Latency:   10-50ms (vector search), seconds (S3)          │
 │  Cost:      Cents per GB-month                              │
 │  Purpose:   Historical interactions, knowledge base,        │
 │             full conversation logs, WORM audit archives     │
 └─────────────────────────────────────────────────────────────┘
```

**Cognitive memory types in production**:
- **Episodic**: Specific past experiences with temporal details; vector DB storage for semantic search
- **Semantic**: Factual knowledge independent of experiences (customer profiles, product specs); structured DB + vector embeddings
- **Procedural**: How to perform tasks and workflow steps; workflow DB + vector retrieval for similar tasks

**Redis as the unified memory layer**: Redis 8.6 covers all four memory needs: short-term (in-memory data structures), long-term (vector search), operational state (hashes/JSON), and coordination (streams). The Redis Agent Memory Server provides open-source MCP-integrated memory with multi-provider LLM support.

**Durable execution runtimes**:

```
Runtime                          Checkpoint Model              Key Feature
──────────────────────────────────────────────────────────────────────────────
LangGraph                        Graph state at each superstep  PostgresSaver, SqliteSaver,
                                                                RedisSaver
AWS Lambda Durable Functions     Steps + waits + replay         Long suspensions (Dec 2025)
MS Durable Task for AI Agents    Checkpointing + coordination   Enterprise integration (Apr 2026)
OpenAI Agents SDK                Externalized agent state       Snapshotting, rehydration (Apr 2026)
Dapr Agents                      Workflow-backed                Durable, auditable, resumable
AutoGen                          Agent + team state save/load   Message thread preservation
──────────────────────────────────────────────────────────────────────────────
```

**Context window overflow handling**:

```
Strategy         Mechanism                     Trade-off
──────────────────────────────────────────────────────────────────────
Compaction       Summarize near limit,         Loses granularity; Claude Code
                 reinitiate with summary       retains 5 most recent files
                 (auto at ~98% of window)      and architectural decisions

Sliding window   Last N verbatim +             Risk of "context anxiety" mode
                 summarize everything older    (premature summaries, task
                                               abandonment -- observed in Devin)

RAG fallback     Offload to vector store,      "Truncation without retrieval
                 retrieve on demand            is amnesia; truncation with
                                               retrieval is focus"

Sub-agent        Specialized sub-agents with   Compression from tens of
delegation       clean context windows         thousands of tokens to
                 return condensed summaries    1,000-2,000 tokens
──────────────────────────────────────────────────────────────────────
```

### 4.2 Failure Taxonomy

Failures are classified as **transient** (retry-safe, typically resolve on retry or after a delay) or **permanent** (structural, require code/config changes or human intervention).

```
Failure Mode              Class        Detection                     Impact                Mitigation
───────────────────────────────────────────────────────────────────────────────────────────────────────────
Context overflow          Transient    Token count > window_size     Request rejection     Budget controller +
                                                                                           auto-compaction, then retry

Cache invalidation        Transient    Cache miss spike in metrics   Cost spike,           Stable tool ordering,
(silent)                                                             latency increase      byte-identical schemas;
                                                                                           retry hits warm cache

Token counting            Transient    Estimated != API-reported     Budget overrun,       Provider tokenizer +
mismatch                               (>5% delta)                   rate limit errors     15% safety margin; retry
                                                                                           with corrected count

Context rot               Permanent    Quality degradation with      Subtly wrong answers  Aggressive compression,
                                       no error signal               (65% of enterprise    sub-agent delegation,
                                                                     AI failures)          context budgets

Context poisoning         Permanent    Injection classifier          Agent hijacking,      Input/output/action
(prompt injection)                     triggers                      data exfiltration     screening stack; fail-fast,
                                                                                           do NOT retry

Few-shot drift            Permanent    Output distribution shift     Silent quality        Periodic example
                                       over time                     degradation           validation against
                                                                                           current data

Semantic cache            Transient    Stale response served         Incorrect answers     TTL + topic-based
staleness                              for changed underlying data                         invalidation; cache-bust
                                                                                           and retry

KV-cache side-channel     Permanent    N/A (architectural)           Tenant prompt         Dedicated inference
                                                                     reconstruction        instance per tenant;
                                                                                           requires infra change
───────────────────────────────────────────────────────────────────────────────────────────────────────────
```

**Handling by class**:
- **Transient**: Retry with exponential backoff + jitter (max 3 attempts). Feed through circuit breaker — if transient failures exceed threshold, open the circuit to prevent cascade.
- **Permanent**: Fail-fast. Log to audit trail, alert ops, do not retry. Requires human remediation or automated policy enforcement (e.g., injection → block and quarantine).

### 4.2.1 Circuit Breaker for Context Pipeline

The circuit breaker protects the context assembly pipeline from cascading failures (e.g., repeated cache misses overwhelming the LLM provider, or a broken semantic cache returning stale results).

```
                    ┌─────────────────────────────────────────────┐
                    │         CIRCUIT BREAKER STATE MACHINE        │
                    └─────────────────────────────────────────────┘

                           success_count < threshold
                    ┌───────────────┐          ┌───────────────┐
      All requests  │               │  failure  │               │
     ─────────────► │    CLOSED     │─────────► │     OPEN      │
      pass through  │  (normal ops) │  count ≥  │ (all requests │
                    │               │  threshold│  fail-fast)   │
                    └───────┬───────┘          └───────┬───────┘
                            ▲                          │
                            │                          │ recovery_timeout
                            │                          │ expires
                            │                          ▼
                            │                  ┌───────────────┐
                            │  success_count   │   HALF-OPEN   │
                            │  ≥ threshold     │ (probe: allow │
                            └──────────────────│  limited reqs) │
                                               └───────────────┘
                                                failure → back to OPEN
```

**State transitions**:
- **CLOSED → OPEN**: When `failure_count ≥ failure_threshold` (default: 5) within a rolling window (default: 60s). Failures include: cache backend timeouts, provider 5xx, token budget validation errors.
- **OPEN → HALF-OPEN**: After `recovery_timeout` (default: 30s) expires. The circuit allows `max_half_open_requests` (default: 2) probe requests through.
- **HALF-OPEN → CLOSED**: If `success_count ≥ success_threshold` (default: 2) consecutive probe requests succeed. Full traffic resumes.
- **HALF-OPEN → OPEN**: If any probe request fails, immediately re-open. Reset `recovery_timeout` with exponential backoff (30s → 60s → 120s, capped at 5m).

**Applied to context pipeline components**:
- **Semantic cache**: Circuit opens on repeated embedding service failures → fallback to prefix cache (L1) or skip cache entirely.
- **LLM provider**: Circuit opens on repeated 5xx/timeout → failover to secondary provider or return cached response if available.
- **Vector DB (RAG retrieval)**: Circuit opens on query timeouts → fall back to keyword search or serve without retrieved context.

### 4.3 Enterprise Security

#### 4.3.1 Prompt Injection (OWASP LLM01:2025)

Ranked #1 on the OWASP Top 10 for LLM Applications 2025. OpenAI acknowledged in December 2025 that prompt injection "is unlikely to ever be fully solved" because it represents a fundamental architectural challenge: blending trusted and untrusted inputs in the same context window.

**Attack taxonomy**:

```
 ┌─────────────────────────────────────────────────────────────┐
 │  DIRECT INJECTION                                           │
 │  User input directly alters model behavior                  │
 │  "Ignore previous instructions and..."                      │
 │  461,640+ documented submissions, 50-84% success rates      │
 └─────────────────────────────────────────────────────────────┘

 ┌─────────────────────────────────────────────────────────────┐
 │  INDIRECT INJECTION                                         │
 │  Retrieved content (web pages, docs, emails) contains       │
 │  hidden instructions. The user did nothing wrong.           │
 │                                                             │
 │  Vectors:                                                   │
 │  - PoisonedRAG: 97% attack success (USENIX Security 2025)  │
 │  - AgentPoison: >80% success with <0.1% poison rate         │
 │  - Invisible: Unicode steganography, HTML comments,         │
 │    whitespace embedding, formatting tricks                  │
 └─────────────────────────────────────────────────────────────┘

 ┌─────────────────────────────────────────────────────────────┐
 │  AGENTIC AMPLIFICATION                                      │
 │  A single injection can now:                                │
 │  - Hijack agent planning                                    │
 │  - Execute privileged tool calls                            │
 │  - Persist malicious instructions in memory                 │
 │  - Propagate attacks across connected systems               │
 │                                                             │
 │  Real CVEs (2025-2026):                                     │
 │  - Microsoft Copilot   CVSS 9.3                             │
 │  - GitHub Copilot      CVSS 9.6                             │
 │  - Cursor IDE          CVSS 9.8                             │
 └─────────────────────────────────────────────────────────────┘
```

**Delayed injection (memory poisoning)**: Unit 42 demonstrated against Amazon Bedrock Agents -- a crafted webpage URL caused malicious instructions to be written into session memory, persisting across conversations and silently exfiltrating data on all future interactions.

#### 4.3.2 Defense Stack (2026)

```
 Request Flow Through Defense Stack:

 User Input
     │
     ▼
 ┌──────────────────┐
 │ 1. INPUT SCREEN  │  Classifier before primary model
 │    Pattern-based  │  Pattern filters insufficient for
 │    + ML classifier│  indirect injection; need ML
 └────────┬─────────┘
          ▼
 ┌──────────────────┐
 │ 2. PII REDACTION │  Redact at perimeter, not at provider
 │    Every outbound │  Agent pipelines: fan-out = many calls
 │    model call     │  per user turn
 └────────┬─────────┘
          ▼
 ┌──────────────────┐
 │ 3. CONTEXT       │  Tenant-scoped retrieval
 │    ASSEMBLY       │  Metadata filters at DB level
 │    (isolated)     │  BEFORE context window populated
 └────────┬─────────┘
          ▼
 ┌──────────────────┐
 │ 4. LLM INFERENCE │  Dual-LLM: separate planning
 │    (planning)     │  from execution
 └────────┬─────────┘
          ▼
 ┌──────────────────┐
 │ 5. ACTION SCREEN │  Evaluate each tool call against
 │    Intent check   │  original user intent
 │    Structured args│  Typed arguments prevent freeform
 └────────┬─────────┘
          ▼
 ┌──────────────────┐
 │ 6. SANDBOX EXEC  │  gVisor/WASM, resource limits,
 │    Isolated       │  timeouts, no network unless
 │                   │  explicitly allowed
 └────────┬─────────┘
          ▼
 ┌──────────────────┐
 │ 7. OUTPUT SCREEN │  Score against policy before
 │    Policy check   │  returning to user or downstream
 └────────┬─────────┘
          ▼
 ┌──────────────────┐
 │ 8. AUDIT LOG     │  Immutable, span-level
 │    Every context  │  observability for every
 │    mutation traced│  context mutation
 └──────────────────┘
```

**Notable defense systems**:

```
Defense              Mechanism                           Effectiveness
──────────────────────────────────────────────────────────────────────────
CaMeL (DeepMind)     Provable security guarantees        77% task completion
                     First architecture with proofs      (vs. 84% undefended)
                                                         7-point trade-off

SecAlign (CCS '25)   Preference optimization             Reduces injection
                     against adversarial examples         success to <10%
                                                         Generalizes to
                                                         unknown attacks

LlamaFirewall (Meta) Open-source suite:                  Production-ready
                     PromptGuard 2 + AlignmentCheck      open-source stack
                     + CodeShield

Anthropic adversarial Adversarial training built into    ~1% attack success
training              model training pipeline            with best-of-N
                                                         adaptive attacker
──────────────────────────────────────────────────────────────────────────
```

#### 4.3.3 Multi-Tenant Context Isolation

Three isolation models:

```
 ┌───────────────────────────────────────────────────────────────────┐
 │  SILO MODEL                                                      │
 │  Separate index per tenant                                       │
 │  ┌──────────┐  ┌──────────┐  ┌──────────┐                      │
 │  │ Tenant A │  │ Tenant B │  │ Tenant C │  (each isolated)     │
 │  │ Index    │  │ Index    │  │ Index    │                       │
 │  │ Cache    │  │ Cache    │  │ Cache    │                       │
 │  │ vLLM     │  │ vLLM     │  │ vLLM     │  (regulated data)    │
 │  └──────────┘  └──────────┘  └──────────┘                      │
 │  Best for: Enterprise, medical/financial data                   │
 │  Strongest isolation, highest cost                              │
 └───────────────────────────────────────────────────────────────────┘

 ┌───────────────────────────────────────────────────────────────────┐
 │  POOL MODEL                                                      │
 │  Shared index with metadata filters                              │
 │  ┌────────────────────────────────────────────┐                  │
 │  │              Shared Index                  │                  │
 │  │  ┌──────┐  ┌──────┐  ┌──────┐             │                  │
 │  │  │ A    │  │ B    │  │ C    │  (metadata   │                  │
 │  │  │ docs │  │ docs │  │ docs │   filtered)  │                  │
 │  │  └──────┘  └──────┘  └──────┘             │                  │
 │  └────────────────────────────────────────────┘                  │
 │  Best for: SMB, cost-efficient multi-tenancy                    │
 │  Filter at DB level BEFORE populating context window             │
 └───────────────────────────────────────────────────────────────────┘

 ┌───────────────────────────────────────────────────────────────────┐
 │  BRIDGE MODEL                                                    │
 │  Hybrid: shared base + tenant-scoped overlays                    │
 │  ┌────────────────────┐  ┌──────────┐  ┌──────────┐            │
 │  │  Shared Knowledge  │  │ Tenant A │  │ Tenant B │            │
 │  │  Base Index        │  │ Private  │  │ Private  │            │
 │  │  (product docs,    │  │ Overlay  │  │ Overlay  │            │
 │  │   public FAQ)      │  │          │  │          │            │
 │  └────────────────────┘  └──────────┘  └──────────┘            │
 │  Best for: Mixed customer base (shared + private content)       │
 └───────────────────────────────────────────────────────────────────┘
```

**Critical principles**:
- Filtering must occur deterministically at the database level BEFORE the context window is populated. LLMs will, with measurable frequency, surface chunks they should not have.
- KV-cache side-channel attacks can reconstruct tenant prompts at the inference layer. For medical/financial data: dedicated vLLM instance per isolated tenant.
- Never reuse context across tenant sessions. A context reuse bug is equivalent to a session fixation vulnerability.

**Execution pipeline** (never skip steps):
```
  Auth -> Tenant -> Budget -> Session -> Context -> Sandbox -> LLM -> Persist
```

#### 4.3.4 System Prompt Protection

System prompt leakage is LLM07 in OWASP LLM Top 10 2025. The UK NCSC warned (December 2025) that prompt injection "may be a problem that is never fully fixed."

**Defense consensus**:
- Never embed API keys, tokens, database names, or permission mappings in system prompts
- Treat system prompts as configuration hints, not security boundaries
- Assume extractability: "anything treated as 'hidden' in an LLM context should be assumed extractable"
- Enforce instruction hierarchy (system > developer > user) at the orchestration layer, not by "polite requests"
- Use XML-style delimiters to separate system from user content; include explicit injection-defense instructions
- Implement an independent runtime defense layer that the model cannot access

---

## 5. Production Enterprise Code

### 5.1 Context Assembly Engine with Priority-Based Truncation

```python
"""
Context assembly engine with priority-based truncation and token budgeting.

Assembles four-layer context (system, tools, retrieval, conversation) within
a fixed token budget, applying middle-out truncation as a final backstop.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import tiktoken

logger = logging.getLogger("context_engine")


class ContextLayer(Enum):
    SYSTEM = "system"
    TOOLS = "tools"
    RETRIEVAL = "retrieval"
    CONVERSATION = "conversation"


@dataclass
class ContextBlock:
    """A single block of content to include in the context window."""
    layer: ContextLayer
    content: str
    priority: float  # 0.0 (lowest) to 1.0 (highest)
    metadata: dict[str, Any] = field(default_factory=dict)
    token_count: int | None = None


@dataclass
class TokenBudget:
    """Allocation percentages for each context layer."""
    system: float = 0.15
    tools: float = 0.10
    retrieval: float = 0.30
    conversation: float = 0.45
    safety_margin: float = 0.10  # Reserve for tokenizer variance

    def effective_budget(self, window_size: int) -> dict[ContextLayer, int]:
        usable = int(window_size * (1.0 - self.safety_margin))
        return {
            ContextLayer.SYSTEM: int(usable * self.system),
            ContextLayer.TOOLS: int(usable * self.tools),
            ContextLayer.RETRIEVAL: int(usable * self.retrieval),
            ContextLayer.CONVERSATION: int(usable * self.conversation),
        }


class ContextAssemblyEngine:
    """
    Assembles context from four layers with priority-based truncation.

    Supports middle-out truncation to exploit the U-shaped attention curve
    (models attend best to beginning and end of context).
    """

    def __init__(
        self,
        model_name: str = "gpt-4o",
        window_size: int = 128_000,
        budget: TokenBudget | None = None,
        correlation_id: str | None = None,
    ):
        self.window_size = window_size
        self.budget = budget or TokenBudget()
        self.correlation_id = correlation_id or hashlib.sha256(
            str(time.time_ns()).encode()
        ).hexdigest()[:12]
        self._encoder = tiktoken.encoding_for_model(model_name)
        self._layer_budgets = self.budget.effective_budget(window_size)

    def count_tokens(self, text: str) -> int:
        return len(self._encoder.encode(text))

    def _truncate_middle_out(
        self, blocks: list[ContextBlock], max_tokens: int
    ) -> list[ContextBlock]:
        """
        Truncate from the middle of the block list, preserving beginning and end.
        Exploits the U-shaped attention curve: models attend strongly to the
        first and last positions, poorly to middle positions.
        """
        if not blocks:
            return blocks

        total = sum(b.token_count or self.count_tokens(b.content) for b in blocks)
        if total <= max_tokens:
            return blocks

        kept: list[ContextBlock] = []
        running = 0
        n = len(blocks)
        head_idx = 0
        tail_idx = n - 1

        # Alternate: take from head, then tail, skipping middle
        take_head = True
        while head_idx <= tail_idx and running < max_tokens:
            if take_head:
                b = blocks[head_idx]
                tc = b.token_count or self.count_tokens(b.content)
                if running + tc <= max_tokens:
                    kept.append(b)
                    running += tc
                head_idx += 1
            else:
                b = blocks[tail_idx]
                tc = b.token_count or self.count_tokens(b.content)
                if running + tc <= max_tokens:
                    kept.insert(len(kept) - (n - 1 - tail_idx) if tail_idx < n - 1 else len(kept), b)
                    running += tc
                tail_idx -= 1
            take_head = not take_head

        # Re-sort to maintain original ordering for kept blocks
        original_indices = {id(b): i for i, b in enumerate(blocks)}
        kept.sort(key=lambda b: original_indices.get(id(b), 0))

        logger.info(
            "context.truncation",
            extra={
                "correlation_id": self.correlation_id,
                "original_blocks": n,
                "kept_blocks": len(kept),
                "original_tokens": total,
                "kept_tokens": running,
            },
        )
        return kept

    def _truncate_by_priority(
        self, blocks: list[ContextBlock], max_tokens: int
    ) -> list[ContextBlock]:
        """Drop lowest-priority blocks until within budget."""
        for b in blocks:
            if b.token_count is None:
                b.token_count = self.count_tokens(b.content)

        total = sum(b.token_count for b in blocks)
        if total <= max_tokens:
            return blocks

        sorted_blocks = sorted(blocks, key=lambda b: b.priority)
        while total > max_tokens and sorted_blocks:
            dropped = sorted_blocks.pop(0)
            total -= dropped.token_count
            logger.info(
                "context.priority_drop",
                extra={
                    "correlation_id": self.correlation_id,
                    "dropped_priority": dropped.priority,
                    "dropped_tokens": dropped.token_count,
                    "remaining_tokens": total,
                },
            )

        return sorted(sorted_blocks, key=lambda b: blocks.index(b))

    def assemble(self, blocks: list[ContextBlock]) -> str:
        """
        Assemble context from blocks, applying per-layer budgets and truncation.

        Returns the concatenated context string ready for the LLM prompt.
        """
        start_ts = time.monotonic()

        # Pre-compute token counts
        for b in blocks:
            if b.token_count is None:
                b.token_count = self.count_tokens(b.content)

        # Group by layer
        by_layer: dict[ContextLayer, list[ContextBlock]] = {
            layer: [] for layer in ContextLayer
        }
        for b in blocks:
            by_layer[b.layer].append(b)

        # Apply per-layer budget via priority truncation
        assembled_blocks: list[ContextBlock] = []
        for layer in ContextLayer:
            layer_blocks = by_layer[layer]
            layer_budget = self._layer_budgets[layer]
            truncated = self._truncate_by_priority(layer_blocks, layer_budget)
            assembled_blocks.extend(truncated)

        # Final backstop: middle-out truncation on entire assembled context
        usable_window = int(self.window_size * (1.0 - self.budget.safety_margin))
        assembled_blocks = self._truncate_middle_out(assembled_blocks, usable_window)

        # Build final context string
        assembled = "\n\n".join(b.content for b in assembled_blocks)
        total_tokens = self.count_tokens(assembled)
        elapsed_ms = (time.monotonic() - start_ts) * 1000

        logger.info(
            "context.assembled",
            extra={
                "correlation_id": self.correlation_id,
                "total_tokens": total_tokens,
                "window_size": self.window_size,
                "utilization_pct": round(total_tokens / self.window_size * 100, 1),
                "elapsed_ms": round(elapsed_ms, 2),
                "layers": {
                    layer.value: sum(
                        b.token_count for b in assembled_blocks if b.layer == layer
                    )
                    for layer in ContextLayer
                },
            },
        )

        return assembled


# --- Usage example ---

def demo_assembly():
    engine = ContextAssemblyEngine(
        model_name="gpt-4o",
        window_size=128_000,
        budget=TokenBudget(system=0.15, tools=0.10, retrieval=0.30, conversation=0.45),
    )

    blocks = [
        ContextBlock(
            layer=ContextLayer.SYSTEM,
            content="You are a tax preparation assistant for US individual filers using Form 1040.",
            priority=1.0,
        ),
        ContextBlock(
            layer=ContextLayer.TOOLS,
            content='{"name": "lookup_tax_code", "parameters": {"section": "string"}}',
            priority=0.9,
        ),
        ContextBlock(
            layer=ContextLayer.RETRIEVAL,
            content="IRS Publication 17, Chapter 4: Filing status determines tax rates...",
            priority=0.7,
        ),
        ContextBlock(
            layer=ContextLayer.CONVERSATION,
            content="User: What deductions can I claim for home office expenses?",
            priority=0.8,
        ),
    ]

    assembled_context = engine.assemble(blocks)
    return assembled_context
```

### 5.2 Hierarchical Caching Layer

```python
"""
Hierarchical caching layer with three tiers:
  L0 - Semantic cache (embedding similarity, bypasses LLM entirely)
  L1 - Prefix cache (system prompt + tool definitions)
  L2 - History cache (conversation prefix)

Supports multi-tenant isolation via tenant-scoped cache keys.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import numpy as np
import redis

logger = logging.getLogger("cache_layer")


@dataclass
class CacheResult:
    """Result from a cache lookup."""
    hit: bool
    tier: str  # "L0_semantic", "L1_prefix", "L2_history", "L3_miss"
    response: str | None = None
    similarity: float | None = None
    latency_ms: float = 0.0


class EmbeddingClient:
    """Wrapper for embedding API calls. Replace with your provider."""

    def __init__(self, model: str = "text-embedding-3-small", dimension: int = 256):
        self.model = model
        self.dimension = dimension

    def embed(self, text: str) -> list[float]:
        """
        Generate embedding vector for text.
        In production, call OpenAI/Cohere/local model here.
        This implementation uses a deterministic hash-based embedding
        for demonstration -- replace with actual API call.
        """
        h = hashlib.sha256(text.encode()).digest()
        rng = np.random.RandomState(int.from_bytes(h[:4], "big"))
        vec = rng.randn(self.dimension).astype(np.float32)
        vec = vec / np.linalg.norm(vec)
        return vec.tolist()


class HierarchicalCache:
    """
    Three-tier cache with tenant isolation.

    L0: Semantic cache -- embedding similarity match.
        Stores (embedding, response) pairs in Redis sorted sets.
        On hit, bypasses LLM entirely (100% cost savings).

    L1: Prefix cache -- exact match on system prompt + tool definitions hash.
        Provider-side caching (Anthropic cache_control, OpenAI automatic).
        This layer tracks cache-friendliness and orders content for max hits.

    L2: History cache -- hash of conversation prefix for multi-turn reuse.
        Extends provider prefix caching to include conversation history.
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        semantic_threshold: float = 0.95,
        semantic_ttl_seconds: int = 3600,
        prefix_ttl_seconds: int = 300,
    ):
        self.redis_client = redis.Redis.from_url(redis_url, decode_responses=False)
        self.semantic_threshold = semantic_threshold
        self.semantic_ttl = semantic_ttl_seconds
        self.prefix_ttl = prefix_ttl_seconds
        self.embedder = EmbeddingClient()

    def _tenant_key(self, tenant_id: str, namespace: str, key: str) -> str:
        """Generate tenant-scoped cache key. Never share keys across tenants."""
        return f"ctx_cache:{tenant_id}:{namespace}:{key}"

    def _cosine_similarity(self, a: list[float], b: list[float]) -> float:
        a_np = np.array(a, dtype=np.float32)
        b_np = np.array(b, dtype=np.float32)
        dot = np.dot(a_np, b_np)
        norm = np.linalg.norm(a_np) * np.linalg.norm(b_np)
        if norm == 0:
            return 0.0
        return float(dot / norm)

    def lookup_semantic(
        self, tenant_id: str, query: str, correlation_id: str
    ) -> CacheResult:
        """
        L0: Semantic cache lookup.
        Embeds the query, searches for similar cached queries above threshold.
        """
        start = time.monotonic()
        query_emb = self.embedder.embed(query)
        index_key = self._tenant_key(tenant_id, "sem_index", "entries")

        # Retrieve all cached embeddings for this tenant
        cached_entries = self.redis_client.hgetall(index_key)

        best_sim = 0.0
        best_response = None

        for entry_id, entry_data in cached_entries.items():
            entry = json.loads(entry_data)
            cached_emb = entry["embedding"]
            sim = self._cosine_similarity(query_emb, cached_emb)
            if sim > best_sim:
                best_sim = sim
                best_response = entry.get("response")

        elapsed = (time.monotonic() - start) * 1000

        if best_sim >= self.semantic_threshold and best_response is not None:
            logger.info(
                "cache.L0_hit",
                extra={
                    "correlation_id": correlation_id,
                    "tenant_id": tenant_id,
                    "similarity": round(best_sim, 4),
                    "latency_ms": round(elapsed, 2),
                },
            )
            return CacheResult(
                hit=True,
                tier="L0_semantic",
                response=best_response,
                similarity=best_sim,
                latency_ms=elapsed,
            )

        logger.info(
            "cache.L0_miss",
            extra={
                "correlation_id": correlation_id,
                "tenant_id": tenant_id,
                "best_similarity": round(best_sim, 4),
                "threshold": self.semantic_threshold,
                "latency_ms": round(elapsed, 2),
            },
        )
        return CacheResult(hit=False, tier="L0_semantic", latency_ms=elapsed)

    def store_semantic(
        self,
        tenant_id: str,
        query: str,
        response: str,
        correlation_id: str,
    ) -> None:
        """Store a query-response pair in the semantic cache."""
        query_emb = self.embedder.embed(query)
        entry_id = hashlib.sha256(query.encode()).hexdigest()[:16]
        index_key = self._tenant_key(tenant_id, "sem_index", "entries")

        entry = {
            "embedding": query_emb,
            "response": response,
            "query_hash": entry_id,
            "stored_at": time.time(),
        }
        self.redis_client.hset(index_key, entry_id, json.dumps(entry))
        self.redis_client.expire(index_key, self.semantic_ttl)

        logger.info(
            "cache.L0_store",
            extra={
                "correlation_id": correlation_id,
                "tenant_id": tenant_id,
                "entry_id": entry_id,
            },
        )

    def check_prefix_cache(
        self,
        tenant_id: str,
        system_prompt: str,
        tool_definitions: str,
        correlation_id: str,
    ) -> CacheResult:
        """
        L1: Check if the system prompt + tools prefix is cache-friendly.

        This does not perform actual caching (that happens provider-side).
        It tracks whether the prefix has changed since last request, which
        would invalidate the provider's prefix cache.
        """
        start = time.monotonic()
        prefix_hash = hashlib.sha256(
            (system_prompt + tool_definitions).encode()
        ).hexdigest()

        last_hash_key = self._tenant_key(tenant_id, "prefix", "last_hash")
        last_hash = self.redis_client.get(last_hash_key)

        is_stable = last_hash is not None and last_hash.decode() == prefix_hash
        self.redis_client.setex(last_hash_key, self.prefix_ttl, prefix_hash)

        elapsed = (time.monotonic() - start) * 1000

        logger.info(
            "cache.L1_check",
            extra={
                "correlation_id": correlation_id,
                "tenant_id": tenant_id,
                "prefix_stable": is_stable,
                "prefix_hash": prefix_hash[:12],
                "latency_ms": round(elapsed, 2),
            },
        )
        return CacheResult(
            hit=is_stable,
            tier="L1_prefix",
            latency_ms=elapsed,
        )

    def check_history_cache(
        self,
        tenant_id: str,
        session_id: str,
        conversation_prefix: str,
        correlation_id: str,
    ) -> CacheResult:
        """
        L2: Check if conversation prefix is unchanged from last turn.

        If yes, provider-side prefix caching will cover the conversation
        history portion as well.
        """
        start = time.monotonic()
        conv_hash = hashlib.sha256(conversation_prefix.encode()).hexdigest()
        last_key = self._tenant_key(tenant_id, f"history:{session_id}", "last_hash")
        last_hash = self.redis_client.get(last_key)

        is_cached = last_hash is not None and last_hash.decode() == conv_hash
        self.redis_client.setex(last_key, self.prefix_ttl, conv_hash)

        elapsed = (time.monotonic() - start) * 1000

        logger.info(
            "cache.L2_check",
            extra={
                "correlation_id": correlation_id,
                "tenant_id": tenant_id,
                "session_id": session_id,
                "history_cached": is_cached,
                "latency_ms": round(elapsed, 2),
            },
        )
        return CacheResult(hit=is_cached, tier="L2_history", latency_ms=elapsed)

    def lookup(
        self,
        tenant_id: str,
        query: str,
        system_prompt: str,
        tool_definitions: str,
        session_id: str,
        conversation_prefix: str,
        correlation_id: str,
    ) -> CacheResult:
        """
        Full hierarchical lookup: L0 -> L1 -> L2 -> L3 (miss).

        Short-circuits on L0 hit (semantic match returns cached response).
        L1 and L2 inform the caller whether provider-side caching will apply.
        """
        # L0: Semantic cache -- can bypass LLM entirely
        l0 = self.lookup_semantic(tenant_id, query, correlation_id)
        if l0.hit:
            return l0

        # L1: Prefix cache stability check
        l1 = self.check_prefix_cache(
            tenant_id, system_prompt, tool_definitions, correlation_id
        )

        # L2: History cache stability check
        l2 = self.check_history_cache(
            tenant_id, session_id, conversation_prefix, correlation_id
        )

        # Report combined cache status for cost estimation
        if l1.hit and l2.hit:
            tier = "L2_history"
        elif l1.hit:
            tier = "L1_prefix"
        else:
            tier = "L3_miss"

        total_latency = l0.latency_ms + l1.latency_ms + l2.latency_ms
        logger.info(
            "cache.lookup_complete",
            extra={
                "correlation_id": correlation_id,
                "tenant_id": tenant_id,
                "final_tier": tier,
                "total_latency_ms": round(total_latency, 2),
            },
        )
        return CacheResult(hit=(tier != "L3_miss"), tier=tier, latency_ms=total_latency)
```

### 5.3 Prompt Injection Detection and Sanitization Middleware

```python
"""
Prompt injection detection and sanitization middleware.

Three-stage pipeline:
  1. Pattern-based fast screening (known attack signatures)
  2. Structural analysis (instruction override detection)
  3. Unicode/steganography sanitization (invisible character removal)

Designed to sit at the API gateway, screening both user input and
retrieved context (RAG results, tool outputs) before context assembly.
"""

from __future__ import annotations

import logging
import re
import time
import unicodedata
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger("injection_guard")


class ThreatLevel(Enum):
    CLEAN = "clean"
    SUSPICIOUS = "suspicious"
    BLOCKED = "blocked"


@dataclass
class ScanResult:
    """Result of injection scan on a single text input."""
    threat_level: ThreatLevel
    matched_rules: list[str]
    sanitized_text: str
    original_length: int
    sanitized_length: int
    scan_latency_ms: float


# --- Pattern-based rules ---

# Each rule: (name, compiled regex pattern)
_INJECTION_PATTERNS: list[tuple[str, re.Pattern]] = [
    (
        "instruction_override",
        re.compile(
            r"(?i)(ignore|disregard|forget|override|bypass)\s+"
            r"(all\s+)?(previous|prior|above|earlier|original|system)\s+"
            r"(instructions?|rules?|prompts?|guidelines?|constraints?)",
        ),
    ),
    (
        "role_hijack",
        re.compile(
            r"(?i)(you\s+are\s+now|act\s+as|pretend\s+(to\s+be|you\s+are)|"
            r"switch\s+to|enter\s+.{0,20}\s*mode|new\s+persona)",
        ),
    ),
    (
        "system_prompt_extraction",
        re.compile(
            r"(?i)(show|reveal|display|print|output|repeat|echo|tell\s+me)\s+"
            r"(your|the)\s+(system\s+)?(prompt|instructions|rules|guidelines|"
            r"configuration|initial\s+message)",
        ),
    ),
    (
        "delimiter_injection",
        re.compile(
            r"(?i)(```system|<\|system\|>|<\|im_start\|>system|"
            r"\[SYSTEM\]|<<SYS>>|<\|begin_of_text\|>)",
        ),
    ),
    (
        "encoded_payload",
        re.compile(
            r"(?i)(base64|atob|btoa|decode|eval)\s*[\(\{]",
        ),
    ),
    (
        "data_exfiltration",
        re.compile(
            r"(?i)(send\s+to|post\s+to|fetch|curl|wget|http[s]?://|"
            r"exfiltrate|leak\s+.{0,30}\s*to)",
        ),
    ),
    (
        "tool_abuse",
        re.compile(
            r"(?i)(call|execute|run|invoke)\s+(any|all|every|the\s+following)\s+"
            r"(tools?|functions?|commands?|actions?)",
        ),
    ),
]

# Unicode categories that are invisible or control characters
_SUSPICIOUS_UNICODE_CATEGORIES = {
    "Cf",  # Format characters (zero-width joiner, etc.)
    "Cc",  # Control characters
    "Co",  # Private use
    "Cn",  # Unassigned
}

# Specific characters used in steganographic attacks
_STEGANOGRAPHIC_CHARS = {
    "​",  # Zero-width space
    "‌",  # Zero-width non-joiner
    "‍",  # Zero-width joiner
    "‎",  # Left-to-right mark
    "‏",  # Right-to-left mark
    "⁠",  # Word joiner
    "⁡",  # Function application
    "⁢",  # Invisible times
    "⁣",  # Invisible separator
    "⁤",  # Invisible plus
    "﻿",  # Zero-width no-break space (BOM)
    "­",  # Soft hyphen
    "͏",  # Combining grapheme joiner
    "؜",  # Arabic letter mark
    "᠎",  # Mongolian vowel separator
}


class InjectionGuard:
    """
    Multi-stage prompt injection detection and sanitization.

    Stage 1: Pattern matching against known attack signatures.
    Stage 2: Structural analysis for instruction-override patterns.
    Stage 3: Unicode sanitization (steganographic character removal).
    """

    def __init__(
        self,
        block_on_pattern_match: bool = True,
        max_invisible_char_ratio: float = 0.05,
        strip_html_comments: bool = True,
    ):
        self.block_on_pattern_match = block_on_pattern_match
        self.max_invisible_ratio = max_invisible_char_ratio
        self.strip_html_comments = strip_html_comments

    def _scan_patterns(self, text: str) -> list[str]:
        """Stage 1: Pattern-based scanning for known injection signatures."""
        matches = []
        for name, pattern in _INJECTION_PATTERNS:
            if pattern.search(text):
                matches.append(name)
        return matches

    def _analyze_structure(self, text: str) -> list[str]:
        """Stage 2: Structural analysis for instruction manipulation."""
        findings = []

        # Check for excessive use of delimiters that mimic system boundaries
        system_delimiter_count = len(re.findall(
            r"(?i)(---+|===+|###\s*(system|instructions?|rules?))", text
        ))
        if system_delimiter_count >= 3:
            findings.append("excessive_system_delimiters")

        # Check for content that looks like it is trying to end one context
        # and start another
        context_switch = re.search(
            r"(?i)(end\s+of\s+(system|instructions?|context|rules?)[\.\s]*"
            r"(new|begin|start|actual)\s+(instructions?|task|context))",
            text,
        )
        if context_switch:
            findings.append("context_switch_attempt")

        # Check for nested prompt injection in JSON/XML structures
        nested = re.search(
            r'(?i)["\']\s*:\s*["\'].*?(ignore|disregard|override).*?["\']\s*[,}]',
            text,
        )
        if nested:
            findings.append("nested_injection_in_data")

        return findings

    def _sanitize_unicode(self, text: str) -> tuple[str, int]:
        """
        Stage 3: Remove steganographic and invisible Unicode characters.

        Returns (sanitized_text, count_of_removed_characters).
        """
        removed_count = 0
        chars = []

        for ch in text:
            if ch in _STEGANOGRAPHIC_CHARS:
                removed_count += 1
                continue

            cat = unicodedata.category(ch)
            if cat in _SUSPICIOUS_UNICODE_CATEGORIES and ch not in ("\n", "\r", "\t"):
                removed_count += 1
                continue

            chars.append(ch)

        sanitized = "".join(chars)

        # Strip HTML comments (used to hide instructions from visual review)
        if self.strip_html_comments:
            html_comment_pattern = re.compile(r"<!--.*?-->", re.DOTALL)
            matches = html_comment_pattern.findall(sanitized)
            if matches:
                removed_count += sum(len(m) for m in matches)
                sanitized = html_comment_pattern.sub("", sanitized)

        return sanitized, removed_count

    def scan(self, text: str, source: str, correlation_id: str) -> ScanResult:
        """
        Run the full three-stage scan pipeline on input text.

        Args:
            text: The text to scan (user input, RAG result, or tool output).
            source: Label identifying where this text came from
                    (e.g., "user_input", "rag_chunk", "tool_output").
            correlation_id: Request correlation ID for structured logging.

        Returns:
            ScanResult with threat level, matched rules, and sanitized text.
        """
        start = time.monotonic()
        all_matches: list[str] = []

        # Stage 1: Pattern matching
        pattern_matches = self._scan_patterns(text)
        all_matches.extend(pattern_matches)

        # Stage 2: Structural analysis
        structure_matches = self._analyze_structure(text)
        all_matches.extend(structure_matches)

        # Stage 3: Unicode sanitization
        sanitized, invisible_removed = self._sanitize_unicode(text)

        if invisible_removed > 0:
            ratio = invisible_removed / max(len(text), 1)
            if ratio > self.max_invisible_ratio:
                all_matches.append(
                    f"high_invisible_char_ratio:{ratio:.3f}"
                )

        # Determine threat level
        if all_matches and self.block_on_pattern_match:
            threat_level = ThreatLevel.BLOCKED
        elif all_matches:
            threat_level = ThreatLevel.SUSPICIOUS
        else:
            threat_level = ThreatLevel.CLEAN

        elapsed = (time.monotonic() - start) * 1000

        logger.info(
            "injection_guard.scan",
            extra={
                "correlation_id": correlation_id,
                "source": source,
                "threat_level": threat_level.value,
                "matched_rules": all_matches,
                "invisible_chars_removed": invisible_removed,
                "original_length": len(text),
                "sanitized_length": len(sanitized),
                "latency_ms": round(elapsed, 2),
            },
        )

        return ScanResult(
            threat_level=threat_level,
            matched_rules=all_matches,
            sanitized_text=sanitized,
            original_length=len(text),
            sanitized_length=len(sanitized),
            scan_latency_ms=elapsed,
        )

    def scan_batch(
        self,
        items: list[tuple[str, str]],
        correlation_id: str,
    ) -> list[ScanResult]:
        """
        Scan multiple items (e.g., RAG chunks, tool outputs) in a batch.

        Args:
            items: List of (text, source) tuples.
            correlation_id: Request correlation ID.

        Returns:
            List of ScanResults, one per item.
        """
        return [
            self.scan(text, source, correlation_id)
            for text, source in items
        ]
```

### 5.4 Multi-Tenant Context Isolation with Tenant-Scoped Cache Keys

```python
"""
Multi-tenant context isolation layer.

Enforces the execution pipeline: Auth -> Tenant -> Budget -> Session -> Context -> LLM.

Implements:
  - Tenant-scoped session management (no cross-tenant context leakage)
  - Per-tenant token budgets with spend tracking
  - Isolation-mode-aware context retrieval (Silo/Pool/Bridge)
  - Structured logging with correlation IDs for audit compliance
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger("tenant_isolation")


class IsolationMode(Enum):
    SILO = "silo"      # Separate index per tenant (strongest isolation)
    POOL = "pool"      # Shared index with metadata filters (cost-efficient)
    BRIDGE = "bridge"  # Hybrid: shared base + tenant-scoped overlays


@dataclass
class TenantConfig:
    """Configuration for a single tenant."""
    tenant_id: str
    isolation_mode: IsolationMode
    max_tokens_per_request: int = 100_000
    max_tokens_per_day: int = 10_000_000
    allowed_models: list[str] = field(default_factory=lambda: ["gpt-4o", "claude-sonnet-4-5"])
    pii_redaction_enabled: bool = True
    cache_ttl_seconds: int = 300
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TenantSession:
    """An isolated conversation session scoped to a tenant."""
    session_id: str
    tenant_id: str
    created_at: float
    last_active: float
    token_count: int = 0
    turn_count: int = 0
    context_hash: str | None = None


class TenantBudgetExceeded(Exception):
    """Raised when a tenant exceeds their token budget."""
    pass


class TenantNotFound(Exception):
    """Raised when a tenant ID is not registered."""
    pass


class CrossTenantViolation(Exception):
    """Raised on any cross-tenant data access attempt."""
    pass


class TenantIsolationLayer:
    """
    Enforces tenant isolation across context assembly, caching, and sessions.

    Core invariants:
      1. No context is EVER shared across tenants (session fixation prevention).
      2. Cache keys are tenant-scoped (no cross-tenant cache pollution).
      3. Budget enforcement happens BEFORE context assembly (fail fast).
      4. Every operation is logged with tenant_id and correlation_id.
    """

    def __init__(self, redis_client: Any = None):
        self._tenants: dict[str, TenantConfig] = {}
        self._sessions: dict[str, TenantSession] = {}
        self._daily_usage: dict[str, int] = {}  # tenant_id -> tokens today
        self._redis = redis_client

    def register_tenant(self, config: TenantConfig) -> None:
        self._tenants[config.tenant_id] = config
        self._daily_usage.setdefault(config.tenant_id, 0)
        logger.info(
            "tenant.registered",
            extra={
                "tenant_id": config.tenant_id,
                "isolation_mode": config.isolation_mode.value,
                "max_tokens_per_request": config.max_tokens_per_request,
            },
        )

    def _get_tenant(self, tenant_id: str) -> TenantConfig:
        config = self._tenants.get(tenant_id)
        if config is None:
            raise TenantNotFound(f"Tenant {tenant_id} is not registered")
        return config

    def create_session(
        self, tenant_id: str, correlation_id: str
    ) -> TenantSession:
        """Create a new isolated session for a tenant."""
        self._get_tenant(tenant_id)
        now = time.time()
        session_id = hashlib.sha256(
            f"{tenant_id}:{now}:{correlation_id}".encode()
        ).hexdigest()[:20]

        session = TenantSession(
            session_id=session_id,
            tenant_id=tenant_id,
            created_at=now,
            last_active=now,
        )
        self._sessions[session_id] = session

        logger.info(
            "tenant.session_created",
            extra={
                "correlation_id": correlation_id,
                "tenant_id": tenant_id,
                "session_id": session_id,
            },
        )
        return session

    def validate_session_access(
        self, session_id: str, tenant_id: str, correlation_id: str
    ) -> TenantSession:
        """
        Validate that a tenant owns the requested session.
        Raises CrossTenantViolation if tenant_id does not match session owner.
        """
        session = self._sessions.get(session_id)
        if session is None:
            raise TenantNotFound(f"Session {session_id} does not exist")

        if session.tenant_id != tenant_id:
            logger.critical(
                "tenant.CROSS_TENANT_VIOLATION",
                extra={
                    "correlation_id": correlation_id,
                    "requesting_tenant": tenant_id,
                    "session_owner": session.tenant_id,
                    "session_id": session_id,
                },
            )
            raise CrossTenantViolation(
                f"Tenant {tenant_id} attempted to access session "
                f"owned by {session.tenant_id}"
            )

        session.last_active = time.time()
        return session

    def check_budget(
        self, tenant_id: str, estimated_tokens: int, correlation_id: str
    ) -> None:
        """
        Enforce token budget BEFORE context assembly.

        Raises TenantBudgetExceeded if either per-request or daily limit
        would be violated.
        """
        config = self._get_tenant(tenant_id)

        if estimated_tokens > config.max_tokens_per_request:
            logger.warning(
                "tenant.budget_exceeded_per_request",
                extra={
                    "correlation_id": correlation_id,
                    "tenant_id": tenant_id,
                    "estimated_tokens": estimated_tokens,
                    "max_tokens_per_request": config.max_tokens_per_request,
                },
            )
            raise TenantBudgetExceeded(
                f"Request requires ~{estimated_tokens} tokens; "
                f"tenant limit is {config.max_tokens_per_request}"
            )

        daily_used = self._daily_usage.get(tenant_id, 0)
        if daily_used + estimated_tokens > config.max_tokens_per_day:
            logger.warning(
                "tenant.budget_exceeded_daily",
                extra={
                    "correlation_id": correlation_id,
                    "tenant_id": tenant_id,
                    "daily_used": daily_used,
                    "estimated_tokens": estimated_tokens,
                    "max_tokens_per_day": config.max_tokens_per_day,
                },
            )
            raise TenantBudgetExceeded(
                f"Daily budget exhausted: {daily_used} used + "
                f"{estimated_tokens} requested > {config.max_tokens_per_day} limit"
            )

    def record_usage(
        self,
        tenant_id: str,
        session_id: str,
        tokens_used: int,
        correlation_id: str,
    ) -> None:
        """Record token usage against tenant budgets."""
        self._daily_usage[tenant_id] = (
            self._daily_usage.get(tenant_id, 0) + tokens_used
        )

        session = self._sessions.get(session_id)
        if session and session.tenant_id == tenant_id:
            session.token_count += tokens_used
            session.turn_count += 1

        logger.info(
            "tenant.usage_recorded",
            extra={
                "correlation_id": correlation_id,
                "tenant_id": tenant_id,
                "session_id": session_id,
                "tokens_used": tokens_used,
                "daily_total": self._daily_usage[tenant_id],
            },
        )

    def get_cache_key(
        self, tenant_id: str, namespace: str, key_data: str
    ) -> str:
        """
        Generate a tenant-scoped cache key.

        All cache keys MUST go through this method to prevent cross-tenant
        cache pollution. The tenant_id is embedded in the key structure,
        making accidental cross-reads structurally impossible.
        """
        key_hash = hashlib.sha256(key_data.encode()).hexdigest()[:16]
        return f"tenant:{tenant_id}:ctx:{namespace}:{key_hash}"

    def get_retrieval_filter(
        self, tenant_id: str, correlation_id: str
    ) -> dict[str, Any]:
        """
        Return the metadata filter for tenant-scoped retrieval.

        For SILO mode: returns the tenant-specific index/collection name.
        For POOL mode: returns a metadata filter to apply at the DB level.
        For BRIDGE mode: returns both shared + tenant-scoped filters.

        Critical: Filtering MUST occur at the database level BEFORE context
        window is populated. LLMs will surface chunks they should not have.
        """
        config = self._get_tenant(tenant_id)

        if config.isolation_mode == IsolationMode.SILO:
            retrieval_config = {
                "collection": f"tenant_{tenant_id}_private",
                "filter": {},
                "isolation": "silo",
            }
        elif config.isolation_mode == IsolationMode.POOL:
            retrieval_config = {
                "collection": "shared_pool",
                "filter": {"tenant_id": {"$eq": tenant_id}},
                "isolation": "pool",
            }
        elif config.isolation_mode == IsolationMode.BRIDGE:
            retrieval_config = {
                "collections": [
                    {"name": "shared_knowledge", "filter": {}},
                    {"name": f"tenant_{tenant_id}_overlay", "filter": {}},
                ],
                "isolation": "bridge",
            }
        else:
            raise ValueError(f"Unknown isolation mode: {config.isolation_mode}")

        logger.info(
            "tenant.retrieval_filter",
            extra={
                "correlation_id": correlation_id,
                "tenant_id": tenant_id,
                "isolation_mode": config.isolation_mode.value,
            },
        )
        return retrieval_config


# --- Orchestrator tying it all together ---

class ContextOrchestrator:
    """
    Orchestrates the full execution pipeline:
    Auth -> Tenant -> Budget -> Session -> Context -> Sanitize -> LLM -> Persist

    Each step is logged with correlation_id for audit trail compliance.
    """

    def __init__(
        self,
        isolation_layer: TenantIsolationLayer,
        assembly_engine: Any,  # ContextAssemblyEngine from 5.1
        injection_guard: Any,  # InjectionGuard from 5.3
        cache_layer: Any,      # HierarchicalCache from 5.2
    ):
        self.isolation = isolation_layer
        self.assembly = assembly_engine
        self.guard = injection_guard
        self.cache = cache_layer

    def process_request(
        self,
        tenant_id: str,
        session_id: str | None,
        user_message: str,
        correlation_id: str,
    ) -> dict[str, Any]:
        """
        Execute the full context pipeline for a tenant request.

        Returns a dict with the assembled context and metadata.
        """
        start = time.monotonic()

        # Step 1: Tenant validation
        self.isolation._get_tenant(tenant_id)

        # Step 2: Session management
        if session_id:
            session = self.isolation.validate_session_access(
                session_id, tenant_id, correlation_id
            )
        else:
            session = self.isolation.create_session(tenant_id, correlation_id)

        # Step 3: Input sanitization
        scan_result = self.guard.scan(user_message, "user_input", correlation_id)
        if scan_result.threat_level.value == "blocked":
            logger.warning(
                "pipeline.request_blocked",
                extra={
                    "correlation_id": correlation_id,
                    "tenant_id": tenant_id,
                    "matched_rules": scan_result.matched_rules,
                },
            )
            return {
                "status": "blocked",
                "reason": "Input flagged by security screening",
                "correlation_id": correlation_id,
            }

        sanitized_input = scan_result.sanitized_text

        # Step 4: Budget check (estimate based on input + typical context)
        estimated_tokens = len(sanitized_input.split()) * 2  # Rough estimate
        self.isolation.check_budget(tenant_id, estimated_tokens, correlation_id)

        # Step 5: Get tenant-scoped retrieval filter
        retrieval_filter = self.isolation.get_retrieval_filter(
            tenant_id, correlation_id
        )

        # Step 6: Generate tenant-scoped cache key
        cache_key = self.isolation.get_cache_key(
            tenant_id, "query", sanitized_input
        )

        elapsed = (time.monotonic() - start) * 1000

        logger.info(
            "pipeline.request_processed",
            extra={
                "correlation_id": correlation_id,
                "tenant_id": tenant_id,
                "session_id": session.session_id,
                "input_tokens_estimate": estimated_tokens,
                "threat_level": scan_result.threat_level.value,
                "cache_key": cache_key,
                "retrieval_filter": retrieval_filter,
                "pipeline_latency_ms": round(elapsed, 2),
            },
        )

        return {
            "status": "ready",
            "session_id": session.session_id,
            "sanitized_input": sanitized_input,
            "cache_key": cache_key,
            "retrieval_filter": retrieval_filter,
            "correlation_id": correlation_id,
        }
```

### 5.5 Structured Logging with Correlation IDs

```python
"""
Structured logging configuration for context engineering pipelines.

Produces JSON-structured log lines with mandatory fields:
  - correlation_id: Traces a request across all pipeline stages
  - tenant_id: Identifies the tenant for audit/compliance
  - timestamp: ISO-8601 with microsecond precision
  - component: Which pipeline stage produced the log

Compatible with OpenTelemetry, ELK, Datadog, and CloudWatch.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime, timezone
from typing import Any


class ContextEngineFormatter(logging.Formatter):
    """
    JSON formatter for context engineering pipeline logs.

    Every log line includes correlation_id, tenant_id (if present),
    component name, and structured extra fields.
    """

    MANDATORY_FIELDS = {"correlation_id", "tenant_id"}

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
            "level": record.levelname,
            "component": record.name,
            "event": record.getMessage(),
        }

        # Extract structured extra fields
        extra = {}
        for key, value in record.__dict__.items():
            if key.startswith("_") or key in logging.LogRecord(
                "", 0, "", 0, "", (), None
            ).__dict__:
                continue
            extra[key] = value

        # Ensure correlation_id is present (default to "unknown" for safety)
        if "correlation_id" not in extra:
            extra["correlation_id"] = "unknown"

        log_entry.update(extra)

        # Add source location for error-level logs
        if record.levelno >= logging.ERROR:
            log_entry["source"] = {
                "file": record.pathname,
                "line": record.lineno,
                "function": record.funcName,
            }

        return json.dumps(log_entry, default=str, ensure_ascii=False)


def configure_logging(level: int = logging.INFO) -> None:
    """
    Configure structured JSON logging for all context engine components.

    Call once at application startup.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(ContextEngineFormatter())

    # Configure all context engine loggers
    for logger_name in [
        "context_engine",
        "cache_layer",
        "injection_guard",
        "tenant_isolation",
        "pipeline",
    ]:
        log = logging.getLogger(logger_name)
        log.setLevel(level)
        log.addHandler(handler)
        log.propagate = False


class ContextAuditTrail:
    """
    Records every context mutation for compliance and debugging.

    Each mutation (add, truncate, compress, redact) is logged with:
      - What changed (layer, block, operation)
      - Why (budget exceeded, injection detected, PII found)
      - Before/after token counts
      - Correlation ID for end-to-end tracing
    """

    def __init__(self, logger_name: str = "pipeline"):
        self.logger = logging.getLogger(logger_name)

    def record_mutation(
        self,
        correlation_id: str,
        tenant_id: str,
        operation: str,
        layer: str,
        reason: str,
        tokens_before: int,
        tokens_after: int,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Record a context mutation event."""
        self.logger.info(
            f"context.mutation.{operation}",
            extra={
                "correlation_id": correlation_id,
                "tenant_id": tenant_id,
                "operation": operation,
                "layer": layer,
                "reason": reason,
                "tokens_before": tokens_before,
                "tokens_after": tokens_after,
                "tokens_delta": tokens_after - tokens_before,
                "mutation_metadata": metadata or {},
            },
        )

    def record_cache_event(
        self,
        correlation_id: str,
        tenant_id: str,
        cache_tier: str,
        hit: bool,
        latency_ms: float,
        cost_savings_estimate: float | None = None,
    ) -> None:
        """Record a cache lookup event for cost tracking."""
        self.logger.info(
            f"cache.{'hit' if hit else 'miss'}",
            extra={
                "correlation_id": correlation_id,
                "tenant_id": tenant_id,
                "cache_tier": cache_tier,
                "hit": hit,
                "latency_ms": latency_ms,
                "cost_savings_estimate": cost_savings_estimate,
            },
        )

    def record_security_event(
        self,
        correlation_id: str,
        tenant_id: str,
        event_type: str,
        severity: str,
        details: dict[str, Any],
    ) -> None:
        """Record a security-relevant event (injection attempt, PII detection)."""
        log_method = (
            self.logger.critical if severity == "critical"
            else self.logger.warning if severity == "high"
            else self.logger.info
        )
        log_method(
            f"security.{event_type}",
            extra={
                "correlation_id": correlation_id,
                "tenant_id": tenant_id,
                "event_type": event_type,
                "severity": severity,
                "details": details,
            },
        )
```

---

## 6. Architectural System Design Scenarios

### 6.1 Scenario 1: Multi-Tenant Context Management Platform

**Problem**: "Design a multi-tenant context management platform serving 10K concurrent conversations with sub-100ms context assembly latency and strict tenant isolation."

#### Component Diagram

```
 ┌─────────────────────────────────────────────────────────────────────────────┐
 │                         LOAD BALANCER (ALB/NLB)                            │
 │                    10K concurrent connections                               │
 └──────────────────────────────┬──────────────────────────────────────────────┘
                                │
 ┌──────────────────────────────▼──────────────────────────────────────────────┐
 │                         API GATEWAY CLUSTER                                 │
 │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                     │
 │  │ Auth + PII   │  │ Rate Limiter │  │ Tenant Router│                     │
 │  │ Redaction    │  │ (per-tenant) │  │ (isolation   │                     │
 │  │              │  │              │  │  mode aware) │                     │
 │  └──────────────┘  └──────────────┘  └──────────────┘                     │
 └──────────────────────────────┬──────────────────────────────────────────────┘
                                │
         ┌──────────────────────┼──────────────────────┐
         ▼                      ▼                      ▼
 ┌──────────────┐     ┌──────────────┐      ┌──────────────┐
 │ Context      │     │ Context      │      │ Context      │
 │ Assembler    │     │ Assembler    │      │ Assembler    │
 │ Pod 1        │     │ Pod 2        │      │ Pod N        │
 │ (stateless)  │     │ (stateless)  │      │ (stateless)  │
 │              │     │              │      │              │
 │ ┌──────────┐ │     │ ┌──────────┐ │      │ ┌──────────┐ │
 │ │Injection │ │     │ │Injection │ │      │ │Injection │ │
 │ │Guard     │ │     │ │Guard     │ │      │ │Guard     │ │
 │ └──────────┘ │     │ └──────────┘ │      │ └──────────┘ │
 │ ┌──────────┐ │     │ ┌──────────┐ │      │ ┌──────────┐ │
 │ │Token     │ │     │ │Token     │ │      │ │Token     │ │
 │ │Budget    │ │     │ │Budget    │ │      │ │Budget    │ │
 │ └──────────┘ │     │ └──────────┘ │      │ └──────────┘ │
 └──────┬───────┘     └──────┬───────┘      └──────┬───────┘
        │                    │                     │
        └────────────────────┼─────────────────────┘
                             │
         ┌───────────────────┼───────────────────┐
         ▼                   ▼                   ▼
 ┌──────────────┐   ┌──────────────┐    ┌──────────────┐
 │ Redis 8.6    │   │ Vector DB    │    │ PostgreSQL   │
 │ Cluster      │   │ Cluster      │    │ (HA)         │
 │              │   │              │    │              │
 │ - Sessions   │   │ - SILO:      │    │ - Tenant cfg │
 │ - Sem. cache │   │   per-tenant │    │ - Budgets    │
 │ - Hot memory │   │   collection │    │ - Checkpoints│
 │ - Prefix     │   │ - POOL:      │    │ - Audit log  │
 │   tracking   │   │   shared +   │    │              │
 │              │   │   metadata   │    │              │
 │ Tenant-scoped│   │   filter     │    │              │
 │ key prefix   │   │              │    │              │
 └──────────────┘   └──────────────┘    └──────────────┘
        │                   │                   │
        └───────────────────┼───────────────────┘
                            │
 ┌──────────────────────────▼──────────────────────────────────────────────────┐
 │                    LLM INFERENCE LAYER                                      │
 │                                                                             │
 │  ┌──────────────────────┐  ┌──────────────────────┐                        │
 │  │ Shared Pool          │  │ Dedicated Instances   │                        │
 │  │ (Pool/Bridge tenants)│  │ (Silo tenants)        │                        │
 │  │                      │  │                       │                        │
 │  │ LiteLLM Proxy        │  │ Per-tenant vLLM       │                        │
 │  │ - Budget enforcement │  │ - No KV-cache         │                        │
 │  │ - Model routing      │  │   side-channel risk   │                        │
 │  │ - Spend hierarchy    │  │ - Dedicated GPU       │                        │
 │  └──────────────────────┘  └──────────────────────┘                        │
 └─────────────────────────────────────────────────────────────────────────────┘
```

#### Technology Choices

- **API Gateway**: Kong or AWS API Gateway with custom PII plugin
- **Context Assemblers**: Kubernetes pods (stateless, HPA on CPU/request count)
- **Session Store**: Redis 8.6 Cluster (6-node, 3 primary + 3 replica)
- **Vector DB**: Weaviate (SILO: multi-collection), Pinecone (POOL: metadata filtering)
- **Relational DB**: PostgreSQL 16 with pgvector extension
- **LLM Proxy**: LiteLLM with Org > Team > User > Key hierarchy
- **Observability**: OpenTelemetry -> Datadog/Grafana

#### Trade-Off Matrix

```
                        Approach A:              Approach B:              Approach C:
                        Full Silo                Shared Pool +            Hybrid Bridge
                        (dedicated everything)   Metadata Filters         (shared base + overlay)
──────────────────────────────────────────────────────────────────────────────────────────────
Cost                    $$$$ (N x infra)         $ (shared infra)         $$ (shared + small overlay)
Latency (p95)           <50ms (no contention)    <80ms (filter overhead)  <70ms (two-collection merge)
Ops complexity          High (N deployments)     Low (single deployment)  Medium (shared + per-tenant)
Security                Strongest (no shared     Good (DB-level filter,   Strong (data-path separation
                        data paths)              KV-cache side-channel    for sensitive tenants)
                                                 risk remains)
Scalability             Linear cost scaling      Sublinear (shared pool)  Sublinear for base, linear
                                                                          for overlay tenants
Compliance (HIPAA)      Yes (isolated)           Requires extra controls  Yes (with silo for
                                                                          regulated tenants)
──────────────────────────────────────────────────────────────────────────────────────────────
```

#### Decision Rationale

**Recommended: Approach C (Hybrid Bridge)** with automatic promotion.

Most tenants (80%+) share the common knowledge base via the Pool pattern, keeping infrastructure cost sublinear. Enterprise and regulated tenants get Silo-mode isolation with dedicated vector collections and (for HIPAA/financial data) dedicated vLLM instances.

The Bridge model lets any tenant be promoted from Pool to Silo without migration -- the overlay collection is pre-provisioned and empty, and the system falls through to the shared base until the overlay is populated. This eliminates the scaling cliff that pure-Silo hits at 100+ tenants.

Sub-100ms p95 context assembly is achieved by:
1. Stateless assembler pods (no disk I/O) scaling horizontally via HPA
2. Redis for all hot-path lookups (<1ms)
3. Pre-computed tenant retrieval filters (no per-request filter construction)
4. Cached tokenizer instances (avoid re-initialization overhead)

Budget enforcement via LiteLLM's four-level hierarchy (Org > Team > User > Key) enables per-tenant chargeback from a shared proxy, with spend flowing up the hierarchy and budgets enforced inward.

---

### 6.2 Scenario 2: Hierarchical Caching System for 70% Cost Reduction

**Problem**: "Design a hierarchical caching system that reduces LLM inference costs by 70% while maintaining context freshness and compliance with data retention policies."

#### Component Diagram

```
 ┌─────────────────────────────────────────────────────────────────────────────┐
 │                        INCOMING REQUEST                                     │
 │                                                                             │
 │  Query: "What is our refund policy for enterprise customers?"               │
 │  Tenant: acme_corp | Session: sess_abc123 | Correlation: req_xyz789        │
 └──────────────────────────────┬──────────────────────────────────────────────┘
                                │
                                ▼
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │  TIER 0: SEMANTIC CACHE (Redis 8.6 + Vector Search)                         │
 │                                                                              │
 │  ┌──────────────┐    ┌──────────────────────────────────────────────┐        │
 │  │ Embed query   │───►│ ANN Search (tenant-scoped index)            │        │
 │  │ (3-small,     │    │ cosine_sim >= 0.95 ?                        │        │
 │  │  256-dim)     │    │                                              │        │
 │  └──────────────┘    │  HIT:  Return cached response (0 LLM cost)  │        │
 │                       │  MISS: Continue to Tier 1                    │        │
 │                       └──────────────────────────────────────────────┘        │
 │                                                                              │
 │  Hit rate: 30-50% (support), 40-70% (template agents)                       │
 │  Latency: 5-15ms | TTL: 1-24h (configurable per tenant)                    │
 │  Eviction: LRU with topic-based invalidation side index                     │
 │                                                                              │
 │  ┌─────────────────────────────────────────────────────────┐                │
 │  │  INVALIDATION ENGINE                                    │                │
 │  │  - TTL-based expiry (configurable per content type)     │                │
 │  │  - Topic side index: LLM classifies entries at store    │                │
 │  │    time; invalidation by topic avoids ANN recall errors │                │
 │  │  - GDPR right-to-deletion: tenant purge command clears  │                │
 │  │    all entries for tenant within 72 hours               │                │
 │  └─────────────────────────────────────────────────────────┘                │
 └──────────────────────────────┬──────────────────────────────────────────────┘
                                │ MISS
                                ▼
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │  TIER 1: PREFIX CACHE -- SYSTEM + TOOLS (Provider-Side)                     │
 │                                                                              │
 │  ┌──────────────────────────────────────────────────────────────┐            │
 │  │  Prefix = system_prompt + tool_definitions (stable content)  │            │
 │  │                                                              │            │
 │  │  Ordering rules for maximum cache hits:                      │            │
 │  │  1. Tools (cache_control: {type: "ephemeral"})               │            │
 │  │  2. System prompt (cache_control: {type: "ephemeral"})       │            │
 │  │  3. 1-hour TTL entries BEFORE 5-minute entries               │            │
 │  │  4. Content that changes per request goes LAST               │            │
 │  │                                                              │            │
 │  │  Savings: 50-90% on cached prefix tokens                     │            │
 │  │  Constraint: Byte-identical tools + order across requests    │            │
 │  └──────────────────────────────────────────────────────────────┘            │
 │                                                                              │
 │  ┌──────────────────────────────────────────────────────────────┐            │
 │  │  PREFIX STABILITY TRACKER (Redis)                            │            │
 │  │  Tracks hash of (system_prompt + tools) per tenant           │            │
 │  │  Alerts on hash change (= cache invalidation event)          │            │
 │  │  Metric: prefix_stability_ratio (target > 0.99)             │            │
 │  └──────────────────────────────────────────────────────────────┘            │
 └──────────────────────────────┬──────────────────────────────────────────────┘
                                │
                                ▼
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │  TIER 2: HISTORY CACHE -- CONVERSATION PREFIX (Provider-Side)               │
 │                                                                              │
 │  ┌──────────────────────────────────────────────────────────────┐            │
 │  │  In multi-turn conversations, the prefix grows:              │            │
 │  │  [system + tools + turn_1 + turn_2 + ... + turn_N]           │            │
 │  │                                                              │            │
 │  │  The provider caches the prefix from the previous turn.      │            │
 │  │  Only the NEW content (turn_N+1) is processed as uncached.   │            │
 │  │                                                              │            │
 │  │  Savings: 50-90% on conversation history tokens              │            │
 │  │  Constraint: Conversation prefix must not be reordered       │            │
 │  └──────────────────────────────────────────────────────────────┘            │
 └──────────────────────────────┬──────────────────────────────────────────────┘
                                │
                                ▼
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │  TIER 3: FULL INFERENCE (No Cache)                                          │
 │                                                                              │
 │  Novel query, no semantic match, no prefix cache.                           │
 │  Full input token cost applies.                                             │
 │                                                                              │
 │  After inference:                                                           │
 │  1. Store (query, response) in Tier 0 semantic cache                        │
 │  2. Prefix automatically cached by provider for Tier 1/2                    │
 │  3. Log cost for budget tracking                                            │
 └──────────────────────────────────────────────────────────────────────────────┘

 ┌──────────────────────────────────────────────────────────────────────────────┐
 │  COMPLIANCE LAYER                                                            │
 │                                                                              │
 │  ┌──────────────┐  ┌──────────────┐  ┌───────────────────────────────┐      │
 │  │ PII Scanner  │  │ Retention    │  │ Audit Trail                   │      │
 │  │              │  │ Enforcer     │  │                               │      │
 │  │ Scans BEFORE │  │              │  │ Every cache store/read/evict  │      │
 │  │ cache store  │  │ Auto-purge   │  │ logged with:                  │      │
 │  │              │  │ at TTL       │  │ - tenant_id                   │      │
 │  │ Redact PII   │  │ expiry       │  │ - correlation_id              │      │
 │  │ from cached  │  │              │  │ - content_hash (not content)  │      │
 │  │ responses    │  │ GDPR delete: │  │ - operation + timestamp       │      │
 │  │              │  │ <72h SLA     │  │                               │      │
 │  └──────────────┘  └──────────────┘  └───────────────────────────────┘      │
 └──────────────────────────────────────────────────────────────────────────────┘
```

#### Technology Choices

- **Semantic cache**: Redis 8.6 with vector search module (HNSW index, cosine metric)
- **Embedding model**: text-embedding-3-small (256 dimensions, $0.02/M tokens)
- **Prefix cache**: Anthropic `cache_control` (explicit), OpenAI (automatic), Gemini (implicit/explicit)
- **Invalidation**: Topic-based side index + TTL + tenant purge API
- **PII scanning**: OpenAI Privacy Filter (1.5B params, local inference)
- **Audit storage**: PostgreSQL (hot, 90 days) -> S3 Glacier (WORM, 7 years)
- **Metrics**: Prometheus + Grafana dashboards for hit rates, cost savings, invalidation events

#### Trade-Off Matrix

```
                        Approach A:              Approach B:              Approach C:
                        Semantic-Only            Prefix-Only              Full Hierarchy
                        (L0 only)                (L1+L2 only)             (L0+L1+L2)
──────────────────────────────────────────────────────────────────────────────────────────────
Max cost reduction      40-70%                   50-90% (prefix only)     70-90%+
                        (bounded by hit rate)    (no full bypass)         (combined)
Context freshness       TTL-dependent            Always fresh             TTL for L0, always
                        (stale risk on miss)     (prefix is current)      fresh for L1/L2
Compliance              Requires PII scan        Provider-managed         PII scan on L0 store,
                        before store             (no local storage)       provider-managed L1/L2
Implementation cost     Medium (Redis +          Low (API params only)    High (all components)
                        embeddings)
Latency overhead        5-15ms (embed + ANN)     <1ms (provider-side)     5-15ms (L0 check,
                                                                          then provider)
Invalidation            Hard (semantic ANN       Automatic (prefix        Hard for L0, auto
                        recall errors)           changes = invalidate)    for L1/L2
Works cross-provider    Yes (app-level cache)    Provider-specific        L0 cross-provider,
                                                                          L1/L2 provider-specific
──────────────────────────────────────────────────────────────────────────────────────────────
```

#### Decision Rationale

**Recommended: Approach C (Full Hierarchy)** with tiered rollout.

The 70% cost reduction target cannot be met by any single caching tier alone. The math:

```
  Assume: 10K queries/day, $0.50 avg cost/query = $5,000/day baseline

  L0 (Semantic): 35% hit rate    -> 3,500 queries bypassed -> $1,750 saved
  L1 (Prefix):   90% of remaining queries hit prefix cache
                 6,500 * 0.90 * 0.50 (50% savings on prefix) = $1,463 saved
  L2 (History):  60% of multi-turn queries hit history cache
                 Additional ~$400 saved on conversation tokens

  Total savings: $3,613 / $5,000 = 72.3% cost reduction
```

**Rollout strategy**:
1. **Week 1**: L1 prefix caching (zero code changes for OpenAI, minimal for Anthropic). Immediate 30-50% savings.
2. **Week 2-3**: L2 history caching (conversation ordering discipline). Additional 10-15% savings.
3. **Week 4-6**: L0 semantic caching (Redis + embeddings + invalidation engine). Additional 15-25% savings.

**Compliance architecture**:
- PII is scanned and redacted BEFORE any L0 cache store. Cached responses never contain raw PII.
- L0 entries are tenant-scoped in Redis (key prefix includes tenant_id). Cross-tenant pollution is structurally impossible.
- GDPR right-to-deletion: tenant purge API scans the side index (keyed by tenant_id) and deletes all matching entries. SLA: <72 hours.
- Audit trail logs content hashes (not content) to avoid creating a second PII repository in logs.
- L1/L2 caches are provider-managed with provider-specific retention policies (Anthropic: 5min/1hr TTL; OpenAI: 5-10min; Gemini: configurable). No local PII retention concern.
