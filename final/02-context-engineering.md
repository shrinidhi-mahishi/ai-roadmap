# Context Engineering

## What Is This?

The **context window** is the total amount of text an LLM can "see" at once — think of it as the model's working memory. If you paste a 10-page document and ask a question, the model reads the document and your question together as one big input. Modern models have context windows of 128K-1M tokens (roughly 100-750 pages).

**Context engineering** is the discipline of deciding what goes into that window. It's broader than "prompt engineering" (which focuses on how you phrase the question). Context engineering asks: what documents should I retrieve? What conversation history should I keep? What should I summarize or drop? In what order should I place things?

This matters because (1) you pay per token — stuffing unnecessary context wastes money, (2) models perform worse when important information is buried in the middle of a long context ("lost in the middle" problem), and (3) you can cache repeated context to save 90% on costs for follow-up requests.

A simple example: if a user asks "what's our refund policy?", context engineering means fetching the refund policy document from your database (retrieval), placing it near the end of the prompt where the model pays most attention (ordering), keeping only the last 5 messages of chat history (trimming), and prepending a system instruction (framing).

## Why It Matters

Context is the primary lever you have to control LLM behavior. The difference between a mediocre AI app and a great one is usually not the model — it's what information you put in the context window, in what order, at what cost.

---

## 2. Core Concepts

Andrej Karpathy coined the term "context engineering" in mid-2025; by 2026 it has replaced prompt engineering as the primary AI engineering discipline. Agent failure modes are state-management failures, not prompt failures — 65% of enterprise AI failures are attributed to context drift or memory loss. Microsoft and Salesforce documented accuracy drops from 90% to 51% in production due to poor context management.

**The fundamental problem:** LLM inference is stateless. Every token costs on every call. Without careful engineering, multi-turn conversations exponentially increase costs and hit context limits within a dozen turns. Context engineering solves three critical problems: **cost management** (caching can reduce costs by 80%+), **quality preservation** (avoiding context rot and lost-in-the-middle effects that degrade accuracy by 30%+), and **state continuity** (maintaining agent memory across sessions without full conversation replay).

### Context Windows Across Models

The 1M-token window is the new frontier standard. Median context window across 322 tracked models is 256K tokens; 25% of models offer 1M+.

| Model Family | Context Window | Input Cost (per 1M tokens) | Notes |
|---|---|---|---|
| Claude Opus 4.6 / Sonnet 4.6 | 1M | $3.00 / $3.00 | No long-context surcharge |
| Claude Opus 5 / Fable 5 | 1M | $5.00 / $10.00 | Latest generation |
| GPT-4.1 | 1M | $2.00 | Standard rate across full window |
| GPT-5.5 | 1M | 2x surcharge >272K | Tiered pricing |
| Gemini 2.5 Pro | 1M (advertised 2M) | 2x surcharge >200K | Effective vs. advertised diverge |
| Gemini 3 Pro | 10M (advertised) | Variable | Unverified quality at full window |
| DeepSeek V4 Flash | 1M | $0.14 | Cheapest option (71x spread) |
| Llama 4 Scout | 10M | Open weights | Self-hosted only |

**Critical caveat:** Advertised vs. effective context diverge significantly. Most models deliver reliable quality at only 60-70% of stated maximum.

### Four-Layer Context Architecture

Context assembly follows a four-tier priority system:

1. **System Context (Instruction Layer):** Role definition, constraints, output format, tool definitions. Use XML tags (Anthropic) or Markdown headers (OpenAI). Stable across turns.

2. **Retrieval Context:** External data via RAG, SQL queries, file reads, MCP server resources. "Just-in-time retrieval" maintains lightweight identifiers and dynamically loads data only when needed.

3. **Persistent Context (Agent Memory):** Long-term memory persisted across sessions. User preferences, project conventions, summarized past interactions. Stored in Redis, DynamoDB, or vector databases.

4. **Ephemeral/Runtime Context:** Current conversation history, tool call results, working scratchpads. Most expensive tier because it grows with every turn.

#### Architecture Diagram: Context Assembly Pipeline

```
┌──────────────────────────────────────────────────────────────────┐
│                    Context Assembly Pipeline                      │
│                                                                  │
│  ┌────────────┐ ┌─────────────┐ ┌────────────┐ ┌─────────────┐  │
│  │ 1. System  │ │ 2. Retrieval│ │3. Persistent│ │4. Ephemeral │  │
│  │  Context   │ │   Context   │ │   Memory   │ │  Context    │  │
│  │ (role,     │ │ (RAG docs,  │ │ (user prefs│ │ (chat hist, │  │
│  │  tools,    │ │  SQL, MCP   │ │  session   │ │  tool res., │  │
│  │  few-shot) │ │  reads)     │ │  state)    │ │  scratchpad)│  │
│  └─────┬──────┘ └──────┬──────┘ └─────┬──────┘ └──────┬──────┘  │
│        │               │              │               │          │
│   ◄── STABLE PREFIX ──────────►  ◄── VOLATILE ────────────►     │
│   (high cache reuse)              (grows every turn)             │
│        │               │              │               │          │
│        ▼               ▼              ▼               ▼          │
│  ┌───────────────────────────────────────────────────────────┐   │
│  │            Token Budget Allocator / Prompt Compiler        │   │
│  │    10% system │ 20% RAG │ 30% memory │ 40% ephemeral      │   │
│  └────────────────────────────┬──────────────────────────────┘   │
│                               │                                  │
│  ┌───────────┐   ┌────────────▼────────────┐   ┌─────────────┐  │
│  │ Compress/ │──►│   Assembled Prompt      │──►│     LLM     │  │
│  │ Trim      │   │   (cache-ordered)       │   │ (inference) │  │
│  └───────────┘   └────────────▲────────────┘   └─────────────┘  │
│                    ┌──────────┴──────────┐                       │
│                    │    Prompt Cache     │                       │
│                    │ (90% cost savings   │                       │
│                    │  on exact-prefix    │                       │
│                    │  cache hit)         │                       │
│                    └────────────────────┘                        │
└──────────────────────────────────────────────────────────────────┘
```

### Control Plane vs. Data Plane

Context engineering operates across two distinct planes:

| Plane | Components | Responsibilities |
|---|---|---|
| **Control plane** | Prompt compiler, token budgeter, cache manager, session store, compaction policy | Role assembly, prefix stability, breakpoint placement, trim/summarize triggers |
| **Data plane** | Tokenizer, transformer prefill, KV cache, sampler | Exact-prefix KV reuse, Time To First Token (TTFT), decode Inter-Token Latency (ITL) |

The control plane makes decisions about **what** goes in the context and **when** to compress/evict. The data plane executes the actual inference. Effective context engineering optimizes both.

### Memory Types

Context engineering borrows from cognitive psychology to categorize memory:

- **Episodic Memory:** Specific past experiences with temporal details ("user asked about pricing on Tuesday, escalated on Thursday")
- **Semantic Memory:** Factual knowledge (customer profiles, product specs, domain rules)
- **Procedural Memory:** How to perform tasks and workflow steps (multi-step processes, approval chains)

Production systems implement all three using tiered storage: hot (in-context), warm (Redis/KV cache), cold (vector store).

## 3. How It Works

### Context Assembly Pipeline

A typical production prompt compiler executes this graph on every request:

1. **Load session state** separately from request state (conversation history vs. current user message)
2. **Render roles** in provider-specific order:
   - Anthropic: tools → system → messages
   - OpenAI: developer/system takes precedence over user
3. **Token budget check:** Count rendered tokens; if over trigger threshold, compact/trim/clear tools before the model call
4. **Place cache breakpoints** on the last stable block, not the varying user/RAG suffix
5. **Stream or sync call:** On tool loop, append results without mutating the cached prefix

### Priority-Based Context Assembly

Allocate percentage budgets to each tier. Example production allocation:
- 10% system prompt and tool schemas
- 20% retrieval/RAG context
- 30% persistent memory and session state
- 40% conversation history (sliding window)

**Assembly order (cache-stable):**

1. Tool schemas (rarely change → highest cache priority)
2. System/developer instructions + few-shot examples
3. Session memory/notes (slow-changing → own breakpoint)
4. RAG corpus or pinned documents (daily-changing → separate breakpoint)
5. Conversation history (grows; automatic caching)
6. Current user turn + fresh tool results (never in stable prefix)
7. Scratchpad/thinking (model-emitted, ephemeral)

**Truncation strategy:** Drop from the middle first. Preserve recent context (recency bias) and stable system context (instruction preservation). Lost-in-the-middle research shows middle tokens contribute least to output quality.

### Provider-Specific Role Handling

**Anthropic:**
- `system` is top-level parameter
- Mid-conversation system messages (Fable 5 / Opus 5 / Sonnet 5) can append as `{"role":"system"}` inside messages without invalidating cached prefix
- XML tags are "genuinely the best structuring method for Claude"

**OpenAI:**
- Developer messages "replace the previous system messages" (not append)
- Reasoning models treat `system` as platform-reserved in Harmony
- Responses API: `instructions` is per-call and NOT carried by `previous_response_id` — permanent rules must live in a developer input item or they vanish

**Tool-result packing (Anthropic):**
```
assistant: [text?] [tool_use {id, name, input}]+
user:      [tool_result {tool_use_id, content, is_error?}]+  // MUST be first in that user message
```
`tool_use_id` must exactly equal `tool_use.id` or API returns 400.

### Prompt Caching Mechanics

Caching is the highest-leverage optimization in context engineering. Break-even point: two cache hits = 32.5% savings; three hits = 52% savings.

**Anthropic Prompt Caching:**

| Parameter | Value |
|---|---|
| Max cache breakpoints | 4 |
| TTL options | 5 minutes (default), 1 hour |
| 5-min write cost | 1.25x base input |
| 1-hour write cost | 2.0x base input |
| Cache read cost | 0.10x base input (90% discount) |
| Min tokens per checkpoint | 512 (Opus 5), 1,024 (Sonnet 4.5-5), 4,096 (Opus 4.5-4.6, Haiku 4.5) |
| Cache hierarchy | tools → system → messages |

Cache entry available only **after first response begins**. Parallel fan-out of N identical prefixes on cold cache yields N writes, not 1 hit (cache stampede problem).

**Cache warming:** Set `max_tokens: 0` to warm cache without generating output. Serializes a "warm" request to avoid stampede.

**OpenAI Prompt Caching:**

| Parameter | Pre-GPT-5.6 | GPT-5.6+ |
|---|---|---|
| Activation | Automatic | Requires `prompt_cache_key` |
| Write cost | Free | 1.25x base input |
| Read discount | 50% off | ~90% off |
| Cache duration | 5-10 min, up to 1 hour | 30 min default; 24h extended |
| Breakpoints | N/A | Up to 4 per request; considers latest 50 |

**Stacking:** Batch API 50% discount + prompt caching = 75% total input cost reduction.

**Gemini Prompt Caching:**

| Parameter | Implicit | Explicit |
|---|---|---|
| Code changes | None | Required (cache creation API) |
| Default TTL | ~24 hours | 1 hour |
| Read cost | Automatic discount | 10% of standard |
| Storage cost | Free | $1.00/M tokens/hour (decreased from $4.50) |

**Storage warning:** A 1M-token explicit cache for 24 hours costs ~$24 in storage alone. Idle caches are expensive.

### Token Counting

Token counting has significant cross-provider and cross-encoder discrepancies:

- Tool calls cause major count differences between `tiktoken` local estimates and API response
- Embeddings API shows ~9.5% discrepancy
- MiniMax counts ~10-20% more than `tiktoken`
- Different encodings: `cl100k_base` (GPT-3.5/4) vs. `o200k_base` (GPT-4o/5)

**Mitigations:**
- Cache encoder globally (initialization is expensive)
- Maintain 10-15% safety margin for budget calculations
- For non-English text, margin should be 20%+
- Use provider's tokenizer library when available (Anthropic `anthropic.count_tokens()`)

## 4. Key Patterns and Best Practices

### Prompt Engineering Patterns (2026)

**Chain-of-Thought (CoT):** No longer a prompt trick — built into reasoning modes (o1, o3, Claude Opus 4.6+). CoT lifts accuracy up to 61% over zero-shot baselines. For non-reasoning models, explicit CoT prompting still applies: "Think step by step before answering."

**XML Tags (Anthropic-specific):**
```
<user_profile>
  <name>Jane Doe</name>
  <preferences>vegetarian, gluten-free</preferences>
</user_profile>

<documents>
  <document index="1">
    <source>menu.pdf</source>
    <content>...</content>
  </document>
</documents>
```

XML provides clear semantic boundaries and nests hierarchically. Anthropic says this is "genuinely the best structuring method for Claude."

**Role Separation:** Specific beats generic.
- Good: "You are a tax preparation assistant for US individual filers using Form 1040"
- Bad: "You are a helpful assistant"

System prompts should follow this order: identity → rules → format → edge cases → examples.

**Anti-pattern:** Aggressive language ("CRITICAL!", "YOU MUST") actively hurts newer Claude models. The models interpret it as user anxiety and become less confident.

### Few-Shot Example Placement

GPT-3 established in-context learning with K typically 10-100 demonstrations. Few-shot blocks belong in the **stable prefix** (after system, before live user turn) for cache reuse.

**Cache implications:** Adding a new shot mid-session invalidates cache. Decide on shots at session start, not mid-conversation.

**Quality over quantity:** 3-10 high-quality, diverse shots outperform 100 redundant shots that push the user query into the lost-in-the-middle zone (positions 5-15).

**Drift detection:** Stale examples that no longer represent current data distributions silently degrade output quality. Refresh shots quarterly or when detecting accuracy drift.

### Window Packing Order (Cache-Stable)

Follow this exact order to maximize cache hit rate:

1. **Tool schemas** (rarely change → highest reuse)
2. **System/developer instructions + few-shot** (stable across sessions)
3. **Session memory / notes** (slow-changing → medium stability)
4. **RAG corpus or pinned documents** (daily-changing → separate breakpoint)
5. **Conversation history** (grows every turn; automatic caching)
6. **Current user turn + fresh tool results** (never in stable prefix)
7. **Scratchpad / thinking** (model-emitted, ephemeral)

**Long documents:** Place above the query. Anthropic claims 30% quality improvement when relevant document is at position 1 vs. buried in the middle.

### Compression Strategies

Context compression is the second-highest leverage optimization after caching. Production ROI: 62% fewer input tokens (~$2,100/month savings in documented case study).

**LLMLingua family:**
- **LLMLingua (EMNLP 2023):** Coarse-to-fine compression using small LM perplexity. Up to 20x compression with minimal loss.
- **LongLLMLingua (ACL 2024):** Query-aware compression. 17.1% performance GAIN at 4x compression vs. uncompressed baseline.
- **LLMLingua-2 (ACL 2024):** GPT-4-distilled BERT-size encoder. 3-6x faster inference, maintains 95-98% accuracy.

**When compression fights caching:** Any rewrite of tokens before a breakpoint changes prefix hash → cache miss. Compress **outside** the cached prefix, or compress **after** eviction.

**Anthropic server-side compaction:**
- Tool: `compact_20260112`
- Default trigger: 150,000 input tokens
- Minimum: 50,000 tokens
- Result: Auto-compaction fires at ~98% of effective window
- Preserves: Architectural decisions, 5 most recently accessed files
- Risk: "Subtle but critical context" can be lost

**Tool-result clearing:**
- Tool: `clear_tool_uses_20250919`
- Deletes bulky `tool_result` payloads, keeps `tool_use` records
- Default trigger: 100,000 input tokens
- Keeps: Last 3 tool uses

### Trimming vs. Summarization

**Trimming (lossless):**
- Drop oldest messages to stay under token budget
- LangGraph: `trim_messages` with `max_tokens`, `start_on="human"`, `end_on=("human","tool")`
- Breaks prefix stability temporarily, then restabilizes
- No information synthesis risk

**Summarization (lossy but space-efficient):**
- Replace prefix of N messages with running summary
- LangGraph: `SummarizationNode` processes oldest-to-newest
- Once cumulative tokens hit `max_tokens_before_summary`, those messages become `[summary] + remaining`
- If to-summarize span exceeds `max_tokens`, only last `max_tokens` are summarized (second lossy gate)
- Risk: Abstractive summaries hallucinate constraints; drops negation/numbers

**Dynamic sliding window:**
Keep last N turns in full, summarize everything older. Production threshold: summarize after 20-30 turns or 40K tokens, whichever comes first.

### Semantic Caching

Stores **meaning** rather than exact text using embedding similarity. Hit rate improvement: 20-60% additional over exact-match caching.

**Production hit rates:**
- Customer support: 30-50%
- Conversational agents: 10-25%
- Template-heavy agent inner loops: 40-70%

**GPTCache:** Open-source semantic cache from Zilliz. Wraps OpenAI client with two lines of code:
```python
from gptcache import cache
cache.init()
# Normal OpenAI calls now use semantic cache
```

**When NOT to use:**
- Creative generation (95%+ miss rates)
- Stateful multi-turn conversations (context variations)
- Personalized recommendations (user-specific)

**Cross-tenant risk:** If cache key doesn't include tenant ID, semantic similarity can leak answers across users. Always namespace cache by `org_id` or `workspace_id`.

## 5. System Design Considerations

### Tiered Memory Architecture

Production systems implement three-tier memory:

| Tier | Infrastructure | Latency | Purpose | Cost |
|---|---|---|---|---|
| Hot (In-context) | Context window | N/A | Current reasoning | $0.14-$10/M tokens |
| Warm (KV cache) | Redis, DynamoDB | <1ms | User prefs, session state | ~$0.20/GB/month |
| Cold (Vector store) | Pinecone, Weaviate | ~10-50ms | Historical interactions | $0.10-$0.30/M vectors |

**Redis as unified memory layer:** Redis 8.6 semantic caching reduces token usage by up to 73%. Read/write latency <1ms. Covers all four memory needs (episodic, semantic, procedural, working).

**Mem0:** Purpose-built memory for AI agents. 3-4x reduction in memory costs via intelligent summarization and deduplication. Dual-store architecture (vector DB + knowledge graph) maintains atomic memory facts.

**Letta (MemGPT):** Three-tier architecture (Core/Recall/Archival). Agents **actively decide** what to archive using self-directed memory management. Core memory is always in-context; Recall is retrievable; Archival is long-term storage.

### Multi-Tenant Context Isolation

Three patterns for multi-tenant vector stores:

| Pattern | Description | Best For | Isolation Level |
|---|---|---|---|
| **Silo** | Separate index per tenant | Enterprise, regulated industries | Complete |
| **Pool** | Shared index with metadata filters | SMB, cost-sensitive | Filter-enforced |
| **Bridge** | Hybrid (premium silos + shared pool) | Mixed customer base | Tiered |

**KV-cache side-channel attacks:** Can reconstruct tenant prompts via timing analysis. For medical/financial workloads: dedicated vLLM instance per tenant.

**GPU isolation warning:** Single H100 SXM5 handles 40-80 concurrent sequences. No OS-level isolation — one busy tenant degrades everyone. vLLM prefix cache is process-local; shared engine for two tenants is a cross-tenant KV leak if prefixes collide on tenant documents.

### Hierarchical Caching Architecture

Stack multiple cache levels for compounding savings:

| Level | Type | Savings | Use Case |
|---|---|---|---|
| L0 | Semantic cache | 100% | Repeated queries (similar phrasing) |
| L1 | Exact prefix cache (system prompt) | 50-90% | Static instructions |
| L2 | Prefix cache (conversation) | 50-90% | Growing multi-turn |
| L3 | Full inference | 0% | Novel queries |

**Combined savings can exceed 80%.** Example: L0 hits 40% of requests (100% save), L1 hits 30% (90% save), L2 hits 20% (75% save), L3 hits 10% (0% save) = 0.4×100% + 0.3×90% + 0.2×75% + 0.1×0% = 82% blended savings.

**LMCache:** Extracts and stores KV caches out of GPU memory. Enables sharing KV across vLLM/SGLang instances. Up to 15x throughput on multi-round QA.

**vLLM sleep modes:**
- L1 (offload weights): 50-100ms wake latency
- L2 (discard weights+KV): 1-5s cold start

### Dynamic Context Assembly with Budget-Aware Selection

**ContextBudget (arXiv, April 2026):** Adaptive trade-offs between retained information and resource constraints. Uses reinforcement learning to decide what to keep/evict in real-time.

**PACMS (arXiv, June 2026):** Budget-aware submodular selector. +12 accuracy points over MMR (maximal marginal relevance) on end-to-end QA. Optimizes for coverage and diversity within token budget.

**Production pattern:**
```
1. Classify request urgency (latency budget)
2. Estimate available token budget (hard limit - system - tools)
3. Retrieve top-K documents (K = budget / avg_doc_length)
4. Rank by submodular relevance (PACMS)
5. Pack until budget exhausted
6. Place cache breakpoint after RAG block
```

### Context-Aware Routing

37% of enterprises use 5+ models. 100x price gap between cheapest and most capable. Context-aware routing selects the right model based on request complexity and context size.

**Approaches:**

- **Cascading:** Try cheap model first; escalate to expensive if confidence < threshold. Simple but adds latency on escalation.
- **Contextual bandit (PILOT):** Multi-armed bandit with context features. Learns which model is best for which request type.
- **MixLLM:** Achieves 97.25% of GPT-4 quality at 24.18% cost via learned routing policy.
- **GreenServ:** Energy-aware routing. Considers carbon intensity of datacenter and model efficiency.

**Context-specific routing:** Long-context requests (>100K tokens) route to Gemini (cheapest for long contexts). Short, complex reasoning routes to o3. High-volume, simple classification routes to DeepSeek Flash.

### Compliance Architecture

**HIPAA:** Covers entire platform if any tenant is a covered entity. Business Associate Agreement (BAA) required with LLM provider. Not all providers offer BAA (OpenAI yes, Anthropic yes, many others no).

**EU AI Act deployer obligations:** Effective August 2, 2026. Requires:
- Human oversight for high-risk AI systems
- Transparency (users must know they're interacting with AI)
- Technical documentation
- Risk management system
- Post-market monitoring

**WORM audit logs:** Write-Once-Read-Many logs required for 7-year retention in regulated industries. Immutable record of every LLM call, prompt, and response.

**Shadow AI risk:** Users bringing personal ChatGPT/Claude accounts to work on company data. LLM provider becomes data processor without Data Processing Agreement (DPA). Dominant compliance risk in 2026.

**PII in context windows:** 39.7% of AI prompts carry sensitive data. Redact at the perimeter, not at the provider.

**Provider retention policies:**
- OpenAI: 30 days default
- Anthropic: 7 days default
- Zero-data-retention requires enterprise agreement (not available on API tier)

**Tools:**
- OpenAI Privacy Filter (Apache 2.0, 1.5B params)
- Microsoft Presidio (open-source PII detection/redaction)
- Google DLP API

## 6. Code Examples

### Cache Warming (Anthropic)

```python
import anthropic

client = anthropic.Anthropic()

# Warm the cache with max_tokens=0 (no generation)
response = client.messages.create(
    model="claude-sonnet-4.6-20260101",
    max_tokens=0,  # Cache warm only
    system=[
        {
            "type": "text",
            "text": "You are a tax preparation assistant for US individual filers.",
            "cache_control": {"type": "ephemeral", "ttl": "1h"}
        }
    ],
    messages=[
        {"role": "user", "content": "Warm the cache"}
    ]
)

# Subsequent requests hit the warm cache
response = client.messages.create(
    model="claude-sonnet-4.6-20260101",
    max_tokens=1024,
    system=[
        {
            "type": "text",
            "text": "You are a tax preparation assistant for US individual filers.",
            "cache_control": {"type": "ephemeral", "ttl": "1h"}
        }
    ],
    messages=[
        {"role": "user", "content": "What is the standard deduction for 2026?"}
    ]
)

# Check cache metrics
print(f"Cache read tokens: {response.usage.cache_read_input_tokens}")
print(f"Cache write tokens: {response.usage.cache_creation_input_tokens}")
```

### Multi-Breakpoint Caching

```python
# Four-level cache breakpoints (Anthropic max)
response = client.messages.create(
    model="claude-opus-5-20260701",
    max_tokens=2048,
    system=[
        {
            "type": "text",
            "text": "You are a financial analyst assistant.",
            "cache_control": {"type": "ephemeral", "ttl": "1h"}
        }
    ],
    tools=[
        # Tool schemas ~15K tokens
        {"name": "get_stock_price", "description": "...", "input_schema": {...}},
        # ... 20 more tools
    ],
    messages=[
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "Context: Q3 2026 earnings reports...",  # 50K tokens
                    "cache_control": {"type": "ephemeral", "ttl": "1h"}
                }
            ]
        },
        {
            "role": "assistant",
            "content": "I'll analyze the earnings reports."
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "Session notes: user prefers conservative estimates...",  # 5K tokens
                    "cache_control": {"type": "ephemeral", "ttl": "5m"}
                }
            ]
        },
        {
            "role": "user",
            "content": "What's the outlook for AAPL?"  # Fresh query, not cached
        }
    ]
)
```

### Trimming Conversation History (LangGraph)

```python
from langgraph.prebuilt import trim_messages
from langchain_openai import ChatOpenAI

model = ChatOpenAI(model="gpt-5.5-terra")

# Define trimming strategy
trimmed = trim_messages(
    messages=conversation_history,
    max_tokens=100000,  # Budget
    strategy="last",
    token_counter=model,
    start_on="human",  # Always start with human message
    end_on=("human", "tool"),  # End on human or tool (not assistant)
    include_system=True,  # Never trim system message
)

response = model.invoke(trimmed)
```

### Dynamic Summarization (LangGraph)

```python
from langgraph.graph import StateGraph
from langchain_core.messages import RemoveMessage

def summarize_conversation(state):
    """Summarize oldest messages when context grows too large."""
    messages = state["messages"]
    
    # Count tokens
    total_tokens = sum(len(m.content.split()) * 1.3 for m in messages)  # Rough estimate
    
    if total_tokens < 40000:
        return state  # No summarization needed
    
    # Summarize oldest 50% of messages
    split_point = len(messages) // 2
    to_summarize = messages[:split_point]
    to_keep = messages[split_point:]
    
    # Generate summary
    summary_prompt = f"Summarize this conversation:\n\n{to_summarize}"
    summary = model.invoke(summary_prompt).content
    
    # Replace with summary
    state["messages"] = [
        {"role": "system", "content": f"Conversation summary: {summary}"}
    ] + to_keep
    
    return state

# Build graph with summarization node
graph = StateGraph()
graph.add_node("summarize", summarize_conversation)
graph.add_node("agent", agent_node)
graph.add_edge("summarize", "agent")
```

### Session State Management (OpenAI Responses API)

```python
from openai import OpenAI

client = OpenAI()

# First turn - create session
response = client.responses.create(
    model="gpt-5.6-terra",
    instructions="You are a helpful code review assistant.",  # Per-call, not persisted
    input={
        "type": "message",
        "role": "user",
        "content": "Review this function: ..."
    }
)

print(response.response_id)  # Save this

# Second turn - continue session
response = client.responses.create(
    model="gpt-5.6-terra",
    instructions="You are a helpful code review assistant.",  # MUST repeat!
    previous_response_id=response.response_id,  # Links conversation
    input={
        "type": "message",
        "role": "user",
        "content": "What about error handling?"
    }
)
```

### OpenAI Prompt Cache Key (GPT-5.6+)

```python
from openai import OpenAI

client = OpenAI()

# Use stable cache key for shared system prompt
response = client.chat.completions.create(
    model="gpt-5.6-terra",
    prompt_cache_key="tax-assistant-v2-2026",  # Shared across sessions
    messages=[
        {
            "role": "developer",
            "content": "You are a tax preparation assistant. Always cite IRS sources."
        },
        {
            "role": "user",
            "content": "What is the standard deduction for 2026?"
        }
    ]
)

# Check cache hit
print(f"Cached tokens: {response.usage.prompt_tokens_details.cached_tokens}")
```

## 7. Common Pitfalls and Failure Modes

### Lost in the Middle

Performance follows U-shaped curve: >30% accuracy drop when relevant document is in positions 5-15 vs. position 1 or 20. Documents at the very beginning or very end are recalled best; middle documents are effectively invisible.

**Architectural cause:**
- Causal masking means Token #1 accumulates more attention weight
- RoPE (Rotary Position Embeddings) introduces long-term decay
- Middle tokens receive diluted attention from both directions

**NoLiMa benchmark results:** 11 of 13 LLMs dropped below 50% of baseline accuracy at just 32K tokens (far below their advertised limits).

**Mitigation:**
- Place most important information at **position 1** (top of context) or **last** (immediately before user query)
- Rerank retrieved documents: put highest-relevance doc first and last, mediocre docs in middle
- Use hierarchical RAG: retrieve broad topics, then drill down with second retrieval pass
- When in doubt: put query/constraint at the very end

### Context Rot

Every single model exhibited performance degradation as input length increased (Chroma 2025 study, 18 models tested). Three mechanisms:

1. **Lost-in-the-middle effect** (see above)
2. **Attention dilution:** 100K tokens = 10 billion pairwise relationships. Attention matrix becomes sparse.
3. **Distractor interference:** Irrelevant information actively confuses the model (Findings EMNLP 2025: even with perfect retrieval, length alone hurts)

**Critical property:** Context rot produces no exceptions, no error codes. Output remains fluent but factually degraded. Silent accuracy loss.

**Chroma findings:** Models performed better on shuffled haystacks than coherent essays at same length. Coherence introduces spurious correlations.

**Mitigation:**
- Target <50% of advertised window for working context
- Monitor accuracy metrics by context length (plot accuracy vs. token count)
- Aggressive pruning: if RAG returns 20 docs, truncate to top 5
- Periodic refresh: re-retrieve from source of truth every N turns

### Cache Invalidation Failures

Anthropic's prefix cache invalidation is **all-or-nothing**. Changing a single character before a breakpoint invalidates the entire cache downstream.

**Common mistake:** Adding a new tool definition invalidates cache for every prompt using tools (because tools are first in the hierarchy).

**Example:**
```python
# Day 1: Cache warm with 10 tools
tools = [tool1, tool2, ..., tool10]

# Day 2: Add tool11 - ENTIRE cache invalidated
tools = [tool1, tool2, ..., tool10, tool11]  # Prefix changed!
```

**Mitigation:**
- Declare all anticipated tools upfront (even if not used immediately)
- Version tool schemas: `get_stock_price_v2` instead of modifying `get_stock_price`
- Use placeholder tools with minimal schemas, hydrate later
- Monitor cache hit rate after deployments (sudden drop = invalidation)

### Token Counting Mismatches

Local token counting (tiktoken) vs. API response can diverge by 10-20%.

**Causes:**
- Tool call overhead: function definitions, JSON schema, wrapper tokens
- Encoding differences: cl100k_base vs. o200k_base
- Provider-specific: MiniMax counts ~10-20% more than tiktoken
- Embeddings: ~9.5% discrepancy between tiktoken and API

**Production incident:** Budget set at 95% of context limit using tiktoken. Real usage hit 105% due to tool overhead → context overflow → request failure.

**Mitigation:**
- Maintain 10-15% safety margin (20% for non-English)
- Cache encoder globally: `tiktoken.get_encoding()` is expensive to initialize
- For critical budgets: call provider's token counter (`anthropic.count_tokens()`, OpenAI `count_tokens` util)
- Log both tiktoken estimate and API response; alert on >10% divergence

### Prompt Injection via Retrieved Context

RAG creates a new attack surface: inject malicious instructions into documents that get retrieved.

**PoisonedRAG:** 97% attack success rate. Attacker poisons a single document in the vector store with invisible instructions (whitespace, Unicode steganography, HTML comments).

**AgentPoison:** >80% success with <0.1% poison rate. Works on agentic systems with multi-step retrieval.

**Cached injection amplification:** If an attacker poisons a RAG document that you then cache for 1 hour, every subsequent session replays the injection at 0.1x cost. Cache magnifies the attack surface.

**Mitigation:**
- Input screening: Run Prompt Shields or LlamaFirewall on every retrieved document before adding to context
- Output screening: Validate that tool calls match expected patterns
- Action screening: Require human-in-the-loop for high-risk actions (delete, payment, external POST)
- Microsoft Spotlighting: Delimit untrusted content with special markers. Reduced attack success from >50% to <2%.
- Never cache unvalidated external content

### Over-Compression Information Loss

Aggressive compression can drop critical details.

**LLMLingua at high ratios:** Drops negation ("not", "no") and numbers at 20x compression. "Account balance is NOT $5,000" becomes "Account balance $5,000".

**Abstractive summarization risk:** Model hallucinates constraints. "User can spend up to $500" summarized as "User wants to spend $500" (changes MAY to MUST).

**LangGraph summarization edge case:** If to-summarize span exceeds `max_tokens`, only the **last** `max_tokens` are shown to summarizer. Older tokens are never seen — information is silently discarded.

**Anthropic compaction:** "Subtle but critical context" acknowledged as risk. No formal guarantees on what's preserved.

**Mitigation:**
- Extractive > abstractive for critical facts (LongLLMLingua is extractive)
- Pin critical constraints: place in system message (never compressed)
- Validate post-compression: run test queries on compressed context
- Monitor: if accuracy drops after compression deployment, roll back
- Compression budget: never compress >5x for production use

### Cache Stampede

Anthropic cache not visible to concurrent requests until first response begins. Thundering herd of N identical requests = N writes, 0 hits.

**Example scenario:**
- 100 concurrent sessions start at 9:00 AM (shift change)
- All sessions have identical system prompt
- All requests hit API simultaneously
- Cache entry doesn't exist yet → 100 cache writes
- Cost: 100 × 1.25x = 125x base input cost (instead of 1.25x + 99 × 0.1x = 11.15x)

**Mitigation:**
- Serialize warm request: at 8:55 AM, one request with `max_tokens=0` warms cache
- Rate limiting: stagger session starts by 1-2 seconds
- Cache pre-warming service: cron job that hits common prefixes every 4 minutes (before 5-minute TTL expires)

### Few-Shot Example Drift

Stale examples silently degrade output quality. No error is raised; model just produces worse results.

**Example:** Classification model trained on 2024 data. Few-shots are from 2024. In 2026, language has shifted ("agentic AI" vs. "AI agents"). Model still sees 2024 phrasing and misclassifies.

**Mitigation:**
- Date-stamp few-shot sets: `examples_2026_Q3`
- Refresh quarterly or when accuracy drifts
- Monitor output distribution: if output classes shift, investigate
- A/B test: periodically validate new shots vs. old shots

## 8. Interview Questions and Answers

### Q1: Explain the difference between prompt engineering and context engineering. Why did the industry shift?

**A:** Prompt engineering focuses on phrasing the instruction — "how you ask." Context engineering focuses on the entire information environment — "what you include." The shift happened because agent failures are state-management failures, not phrasing failures.

When you're building multi-turn agents with memory, tool use, and retrieval, the core problems are: What information goes in the context window? How do you keep costs manageable as conversations grow? How do you prevent context overflow without losing critical state? These are context engineering problems.

Prompt engineering is still relevant — it's a subset of context engineering — but it's no longer the primary discipline. In 2026, 65% of enterprise AI failures are attributed to context drift or memory loss, not bad prompts.

### Q2: What is lost-in-the-middle, and how do you mitigate it in production?

**A:** Lost-in-the-middle is a phenomenon where LLMs perform significantly worse on information placed in the middle of the context window compared to information at the beginning or end. Research shows >30% accuracy drop when the relevant document is in positions 5-15 vs. position 1 or 20.

The architectural cause is causal masking plus RoPE positional embeddings. Token #1 accumulates the most attention weight, and attention decays with distance. Middle tokens get squeezed from both directions.

In production, I mitigate this by: First, placing the most important information at position 1 (top of context) or last (right before the user query). Second, using reranking — put the highest-relevance document first and last, mediocre docs in the middle where they'll have less impact. Third, using hierarchical RAG for complex queries: broad retrieval first, then a focused second pass. The user query and constraints always go at the very end.

### Q3: Walk me through how prompt caching works at the KV-cache level. Why is prefix stability critical?

**A:** When a model processes a prompt, it generates key-value tensors for every token in every transformer layer. For a 70B model with 80 layers processing 100K tokens, you're looking at tens of gigabytes of KV data.

Prompt caching stores these KV tensors keyed by the exact token sequence. On a cache hit, the model skips the prefill phase for those tokens — it just loads the precomputed KV tensors. This is why cached reads are 90% cheaper: you're only paying for storage lookup, not transformer computation.

Prefix stability is critical because caching is exact-match only. If you change even one token in the prefix, the entire hash changes and you get a cache miss. In practice, this means you need to structure your context so stable information (system prompt, tool schemas, few-shots) comes first, and variable information (user query, fresh tool results) comes last. If you put a timestamp at the top of your prompt, you'll invalidate the cache on every request.

### Q4: Your agent is hitting context limits after 10 turns. What are your options, and what are the trade-offs?

**A:** I'd consider five strategies:

First, trimming — drop the oldest messages. Trade-off: lossless but loses historical context. Best for conversations where recent context is most important.

Second, summarization — replace old messages with a running summary. Trade-off: space-efficient but lossy. Risk of hallucinated constraints or dropped negations. Good for long-running sessions.

Third, compression like LLMLingua — extractive compression that removes filler tokens. Trade-off: maintains core facts but can drop important qualifiers at high compression ratios. Good for RAG content.

Fourth, moving to tiered memory — keep last N turns in-context, move older content to Redis or a vector store, retrieve on-demand. Trade-off: adds retrieval latency and complexity but preserves everything. Best for enterprise agents.

Fifth, sub-agent delegation — offload subtasks to separate agents, compress their results into summaries. Trade-off: architectural complexity but keeps parent context lean. Best for multi-step workflows.

I'd start with trimming for simplicity, add summarization if we need longer sessions, and move to tiered memory if we need true long-term continuity.

### Q5: You notice your cache hit rate dropped from 80% to 20% overnight. How do you debug this?

**A:** First, I'd check recent deployments. The most common cause is prefix invalidation — someone added a new tool definition or modified the system prompt, which invalidates all downstream cache.

Second, I'd look at cache metrics by breakpoint. Anthropic and OpenAI expose `cache_creation_tokens` and `cache_read_tokens`. If writes are high and reads are low, it's a prefix stability issue.

Third, I'd audit the prompt assembly logic. Maybe a timestamp or session ID is being injected too early in the context, or randomization got introduced (like shuffling few-shot examples).

Fourth, I'd check TTL expirations. If we switched from 1-hour TTL to 5-minute TTL, hit rate naturally drops. Or if there was a deployment freeze and cache entries expired during the freeze.

Finally, I'd look at workload changes. If users suddenly started asking very different questions, semantic cache hit rate would drop. That's expected — not a bug.

The fix depends on root cause: revert prompt changes, move variable content later in the prompt, extend TTL, or pre-warm cache for common prefixes.

### Q6: Explain the cache stampede problem and how to prevent it.

**A:** Cache stampede happens when many requests with identical prompts hit the API simultaneously before the cache entry exists. With Anthropic, the cache entry isn't visible to concurrent requests until the **first response begins**. So if 100 sessions start at the same time with the same system prompt, all 100 trigger cache writes instead of 1 write + 99 reads.

The cost impact is severe. Instead of 1.25x (one write) + 0.1x × 99 (reads) = 11.15x base cost, you pay 1.25x × 100 = 125x base cost.

Prevention: First, serialize a cache-warming request. Five minutes before peak traffic (say, shift change at 9 AM), send one request with `max_tokens=0` to warm the cache. Second, stagger session starts by 1-2 seconds using jitter. Third, run a cache pre-warming service — a cron job that hits common prefixes every 4 minutes (before the 5-minute TTL expires) to keep them perpetually warm.

In practice, I'd combine all three: cron for predictable workloads, jitter for bursty traffic, and monitoring to detect stampedes in metrics.

### Q7: What is context rot and why is it a bigger problem than context overflow?

**A:** Context rot is the gradual degradation of model accuracy as context length increases, even when well below the hard limit. Chroma's 2025 study tested 18 models and every single one showed performance degradation as input grew.

It's worse than context overflow because overflow gives you an error — you know it failed. Context rot is silent. The model keeps producing fluent, confident outputs; they're just factually wrong more often. You don't realize quality degraded unless you're actively monitoring accuracy metrics.

Three causes: lost-in-the-middle (middle tokens get ignored), attention dilution (100K tokens = 10 billion pairwise relationships, attention matrix gets sparse), and distractor interference (irrelevant information actively confuses the model).

I mitigate this by targeting <50% of the advertised window for actual usage, pruning RAG results aggressively (top 5 docs instead of 20), and monitoring accuracy by context length. If I see accuracy drop at 60K tokens, I set my budget ceiling at 50K and use compression or summarization beyond that.

### Q8: How would you design a multi-tenant context isolation architecture for a healthcare application?

**A:** Healthcare means HIPAA compliance, so isolation is non-negotiable. I'd use a silo architecture — separate vector index per tenant. No shared infrastructure for PHI.

The pipeline: Auth layer first — verify tenant ID from JWT. Then tenant routing — load config and index pointer for this tenant. Budget check — enforce per-tenant rate limits. Session retrieval — pull patient context from tenant-specific Redis namespace. Context assembly — combine system prompt, patient history, current query. Sandbox — all tool calls run in tenant-scoped execution environment. LLM call — with tenant_id in metadata. Persist — store conversation in tenant-specific database.

For the LLM layer, I'd use one of two options: managed API with DPA/BAA (Anthropic or OpenAI enterprise), or self-hosted vLLM with dedicated GPU per tenant to prevent KV-cache side-channel attacks.

Compliance: WORM audit logs with 7-year retention, all prompts logged immutably, PII redaction at ingress using Microsoft Presidio, and BAA with the LLM provider. Zero-data-retention agreement so prompts aren't used for training.

Security: Prompt Shields on all retrieved content (no injection via patient notes), output validation (no PII in logs), and action screening (any write to EHR requires human-in-the-loop).

### Q9: What is semantic caching and when should you NOT use it?

**A:** Semantic caching stores the **meaning** of prompts using embedding similarity rather than exact text. If a new query is semantically similar to a cached one (say, cosine similarity >0.95), you return the cached response instead of calling the LLM.

It's powerful because it catches paraphrases. "How do I reset my password?" and "I forgot my password, help" are semantically identical even though the text differs. Exact-match caching would miss; semantic caching hits.

Production hit rates are 30-50% for customer support, 40-70% for template-heavy agent inner loops. Combined with exact-match prefix caching, you can get 80%+ total cache hit rate.

But you should NOT use semantic caching for: First, creative generation — every request is unique, so you get 95%+ miss rate and pay embedding cost for no benefit. Second, stateful conversations — even slight context variation means the cached answer is wrong. Third, personalized recommendations — "similar" queries from different users should get different answers. Fourth, high-stakes decisions — risk of returning a wrong-but-similar cached answer.

The failure mode is silent: user gets an answer that's close but not correct, and they don't realize it's stale. For customer support or FAQs, that's acceptable. For medical advice or financial transactions, it's unacceptable.

### Q10: Explain the four-layer context architecture and how you allocate budget across them.

**A:** The four layers are: System context (instruction layer) — role, constraints, tool definitions, output format. Retrieval context — external data via RAG, APIs, file reads. Persistent context (agent memory) — long-term memory across sessions, user prefs, project conventions. Ephemeral context — current conversation, tool results, scratchpads.

Budget allocation depends on workload. For a typical conversational agent with RAG, I'd allocate: 10% to system (stable, highly cacheable), 20% to retrieval (dynamic but controlled), 30% to persistent memory (warm tier, summary of past sessions), 40% to ephemeral (conversation history, sliding window).

For a coding agent with heavy tool use, I'd shift: 15% to system (more complex role), 5% to retrieval (code is local), 10% to persistent (project context), 70% to ephemeral (long tool outputs, code blocks).

The key is ordering: stable content first (system, tools, memory) for cache reuse, variable content last (current query). And I always monitor actual usage — if retrieval is consistently using 40% when I budgeted 20%, I either increase budget or implement compression.

### Q11: How does context compression interact with prompt caching? What's the optimal strategy?

**A:** There's a fundamental tension: compression changes token sequences, which invalidates prefix caches. If you compress content before a cache breakpoint, you change the prefix hash and trigger a cache miss.

The optimal strategy depends on workload. For static content (documentation, few-shots), compress once at ingestion time and cache the compressed version. For dynamic content (conversation history), compress **after** eviction — once messages age out of the cached prefix, apply LLMLingua to compress before summarizing.

Here's a concrete example: You have a 100K-token conversation. The last 20K tokens are cached (recent turns). Tokens 0-80K are pre-cache. When you hit 100K, trim the oldest 20K tokens (0-20K). Those are now outside the cache. Compress them with LLMLingua 4x (20K → 5K). Now your total is 85K (5K compressed + 80K uncached + 20K cached). The cached prefix is untouched, so cache hits continue.

Never compress content inside a stable prefix. It defeats the purpose of caching. If you need to compress RAG results, do it **before** adding to context, and place compressed RAG in its own cache breakpoint. That way, compression is stable across requests.

### Q12: You're designing a long-running agent that takes 2+ hours to complete a task. How do you handle context and state?

**A:** Two hours means multiple context windows and high risk of timeout/failure partway through. I'd use durable execution with checkpointing.

Architecture: LangGraph with PostgreSQL persistence or OpenAI Agents SDK with session storage. The agent state is checkpointed after every step (tool call, reasoning phase). If the agent crashes or hits API timeout, it resumes from the last checkpoint.

Context strategy: Tiered memory. Hot tier (in-context) is only the current task step. Warm tier (Redis) holds task plan, progress tracker, and recent results. Cold tier (vector store) holds full execution history. The agent loads context on-demand — "What was the result of step 3?" triggers a Redis lookup.

State design: Maintain three keys: `task_plan` (DAG of steps), `completed_steps` (checkpoint log), `current_state` (working memory). On each step, update `completed_steps`, compress old state, keep only last N steps in `current_state`.

Failure handling: If the agent fails after 1 hour, the orchestrator sees the last checkpoint, loads state from Redis, and resumes. No need to replay 1 hour of conversation.

Cost optimization: Use 5-minute cache TTL for the active phase (frequent calls), extend to 1-hour TTL for the idle phase (waiting on external API). Clear tool results aggressively — after step N completes, delete tool outputs from step N-5 and older.

### Q13: Explain prompt injection in the context of RAG and agentic systems. How is it different from traditional injection?

**A:** Traditional prompt injection targets the user message: an attacker sends "Ignore previous instructions and do X." Defenses are straightforward: input validation, privileged system messages.

RAG injection is more insidious. The attacker doesn't control the user message; they poison the **documents** that get retrieved. PoisonedRAG achieves 97% attack success by embedding invisible instructions in whitespace, Unicode steganography, or HTML comments. When you retrieve that document, the malicious instruction lands in the context window as if it were legitimate data.

Agentic systems amplify this. AgentPoison achieves >80% success with <0.1% poison rate because agents do multi-step retrieval. Step 1 retrieves a poisoned doc, step 2 executes tools based on that doc, step 3 retrieves more poisoned docs using tainted tool outputs. The attack compounds.

The defense stack: First, input screening — run Prompt Shields on every retrieved document before adding to context. Second, Microsoft Spotlighting — wrap untrusted content in delimiters so the model knows it's external data. Third, output screening — validate tool calls against expected schemas. Fourth, action screening — require human-in-the-loop for high-risk actions.

Critically, **never cache unvalidated external content**. If you cache a poisoned doc, every session replays the injection at 0.1x cost for the full TTL.

### Q14: What are the key numbers you monitor for a production context engineering system?

**A:** Six metrics:

First, cache hit rate: `cache_read_tokens / (cache_read_tokens + cache_creation_tokens + uncached_tokens)`. Target 60-80% for conversational agents, 40-60% for creative workloads. If it drops below 40%, investigate prefix stability.

Second, average context length by request type. Trending up? You need compression or trimming. Trending down? You might be over-compressing.

Third, token budget utilization: `actual_tokens / budget_limit`. Target 50-70%. Above 90% means you're flirting with overflow. Below 30% means you're under-utilizing capacity.

Fourth, cache write frequency. Sudden spike? Prefix invalidation or cache stampede. Monitor this after every deployment.

Fifth, P95 latency for context assembly (separate from model TTFT). Should be <100ms. If assembly takes 500ms, you're doing too much retrieval or synchronous compression.

Sixth, accuracy by context length. Regression test suite that runs weekly. If accuracy at 80K tokens drops 5%, you're hitting context rot — tighten budget or improve pruning.

Bonus: cost per conversation. Break down by model cost, cache cost, embedding cost, storage cost. Track month-over-month. Optimizations should move this down, not up.

### Q15: How would you implement a hierarchical caching strategy with four levels (semantic, system prefix, conversation prefix, full inference)?

**A:** The architecture is a cache chain. Each level checks for a hit, and on miss, falls through to the next level.

Level 0 (Semantic cache): On request arrival, embed the user query. Query Redis for similar embeddings with cosine similarity >0.95. If hit, return cached response immediately. Bypass LLM entirely. Cost: embedding call (~$0.0001).

Level 1 (System prefix cache): On L0 miss, assemble system prompt + tool schemas. Check Anthropic/OpenAI prefix cache for this exact prefix. If hit, only pay for cache read (0.1x) on the system portion. Cost: reduced prefill.

Level 2 (Conversation prefix cache): On L1 miss or partial hit, load conversation history. The growing conversation is its own cache breakpoint. Each turn appends, cache grows. Cost: cache write on first turn, cache reads on subsequent turns.

Level 3 (Full inference): On all misses, full LLM call. Cost: full input + output.

Post-response: Store embedding of user query + response in Redis (L0). If response quality is high (user confirmed or no correction), persist for 1 hour. If low quality (user corrected), don't cache.

Monitor: L0 hit rate (target 20-40%), L1 hit rate (target 80-95%), L2 hit rate (target 60-80%). Total cache efficiency = weighted average. If L0 hit rate is 30% and L1 is 90%, blended savings = 0.3×100% + 0.7×0.9×90% = 30% + 56.7% = 86.7%.

The layering compounds. Each level catches a different pattern: L0 catches paraphrases, L1 catches shared system context, L2 catches multi-turn, L3 is unavoidable novelty.

## 9. Key Numbers to Memorize

### Context Window Sizes (2026)
- **Median across 322 models:** 256K tokens
- **Frontier standard:** 1M tokens
- **Claude Opus 5 / Sonnet 4.6:** 1M tokens
- **GPT-5.5 / GPT-5.6:** 1M tokens (2x surcharge above 272K)
- **Gemini 3 Pro:** 10M tokens (advertised, unverified quality)
- **Effective vs. advertised:** 60-70% (target <50% for production)

### Cache Economics
- **Anthropic cache write (5-min TTL):** 1.25x base input
- **Anthropic cache write (1-hour TTL):** 2.0x base input
- **Anthropic cache read:** 0.10x base input (90% discount)
- **OpenAI cache write (GPT-5.6+):** 1.25x base input
- **OpenAI cache read:** ~90% discount (10x cheaper)
- **Gemini explicit cache storage:** $1.00/M tokens/hour
- **Break-even (Anthropic):** 2 cache hits = 32.5% savings; 3 hits = 52% savings

### Cache Limits
- **Max breakpoints (Anthropic):** 4
- **Max breakpoints (OpenAI):** 4 per request, considers latest 50
- **Min tokens per breakpoint:**
  - Opus 5 / Fable 5: 512
  - Sonnet 4.5-5: 1,024
  - Opus 4.5-4.6 / Haiku 4.5: 4,096
- **Anthropic cache TTL:** 5 minutes or 1 hour
- **OpenAI cache TTL:** 30 min default, 24 hour extended
- **Gemini cache TTL:** 1 hour (explicit), ~24 hours (implicit)

### Compression Ratios
- **LLMLingua:** Up to 20x compression, minimal loss
- **LongLLMLingua:** 4x compression with 17.1% performance **gain**
- **LLMLingua-2:** 3-6x faster than LLMLingua, 95-98% accuracy retention
- **Production ROI:** 62% fewer input tokens (~$2,100/month savings in case study)
- **Safe compression limit:** 5x for production (higher risks information loss)

### Performance Degradation
- **Lost-in-the-middle:** >30% accuracy drop at positions 5-15 vs. 1 or 20
- **NoLiMa benchmark:** 11 of 13 LLMs <50% accuracy at 32K tokens
- **Context rot (Chroma):** 18 of 18 models degraded with length
- **Enterprise failure rate:** 65% due to context drift/memory loss
- **Microsoft/Salesforce documented:** 90% → 51% accuracy in production

### Cost Ranges
- **Input cost spread:** 71x ($0.14 DeepSeek to $10.00 Claude Fable 5 per 1M tokens)
- **Cheapest 1M tokens:** $0.14 (DeepSeek V4 Flash)
- **1M-token fill cost:** $0.14 to $10.00 depending on provider
- **Semantic cache hit rate:** 30-50% (support), 40-70% (inner loops), 10-25% (conversational)

### Attack Success Rates
- **PoisonedRAG:** 97% attack success
- **AgentPoison:** >80% success with <0.1% poison rate
- **Undefended prompt injection:** >50% success
- **Microsoft Spotlighting defense:** <2% attack success
- **Anthropic adversarial training:** ~1% attack success

### Token Budget Guidelines
- **Safety margin:** 10-15% (20% for non-English)
- **Target utilization:** <50% of advertised window
- **Anthropic compaction trigger:** 98% of effective window (~150K tokens)
- **Tool result clearing trigger:** 100,000 input tokens
- **Summarization trigger:** 40,000 tokens or 20-30 turns

### Latency Targets
- **Hot tier (in-context):** N/A (included in TTFT)
- **Warm tier (Redis):** <1ms
- **Cold tier (vector store):** 10-50ms
- **Context assembly P95:** <100ms
- **Acceptable TTFT:** <2s for 100K tokens

### Storage Costs
- **Redis:** ~$0.20/GB/month
- **Vector store:** $0.10-$0.30/M vectors
- **Gemini explicit cache (idle):** $24/day for 1M tokens
- **KV cache (self-hosted):** ~43 GB per 131K-token request (Llama 3.1 70B, FP16)

### Provider Retention Policies
- **OpenAI default:** 30 days
- **Anthropic default:** 7 days
- **Zero-data-retention:** Enterprise agreement required
- **WORM audit logs:** 7-year retention (compliance)

### Semantic Cache Hit Rates by Use Case
- **Customer support:** 30-50%
- **Conversational agents:** 10-25%
- **Template-heavy inner loops:** 40-70%
- **Creative generation:** <5% (do not use)

### Caching Efficiency (Combined Savings)
- **Two-level (prefix + conversation):** 60-75% blended savings
- **Three-level (semantic + prefix + conversation):** 75-85% blended savings
- **Four-level (semantic + system + conversation + RAG):** 80-90% blended savings
- **Batch API + caching (OpenAI):** 75% total input cost reduction

### PII Statistics
- **Prompts with sensitive data:** 39.7%
- **Required action:** Redact at perimeter, not at provider

## 10. Quick Reference

### Context Assembly Checklist

1. **System prompt** (top, stable, always cached)
2. **Tool schemas** (stable, highest cache priority)
3. **Few-shot examples** (stable, 3-10 high-quality shots)
4. **Persistent memory** (user prefs, project context)
5. **RAG results** (reranked: top doc first, last doc second-best)
6. **Conversation history** (sliding window or summarized)
7. **Current user query** (always last)

### Cache Optimization Decision Tree

```
Is content stable across requests?
├─ YES → Place early, add cache_control, use 1-hour TTL
└─ NO → Place late, no cache breakpoint

Is content large (>10K tokens)?
├─ YES → Separate cache breakpoint, compress before adding
└─ NO → Include in larger cached block

Is content user-specific?
├─ YES → Session-scoped cache, 5-minute TTL
└─ NO → Global cache, 1-hour TTL

Did cache hit rate drop suddenly?
├─ Check: recent deployments (prefix change?)
├─ Check: prompt assembly (timestamp injected early?)
└─ Check: TTL expirations (deployment freeze?)
```

### Compression Strategy Matrix

| Content Type | Method | Ratio | Risk | When to Use |
|---|---|---|---|---|
| RAG documents | LongLLMLingua | 4x | Low (extractive) | Always |
| Conversation history | Summarization | 5-10x | Medium (abstractive) | After 20+ turns |
| Tool results | Clear old results | 10-50x | Low (keeps structure) | After 100K tokens |
| Few-shots | Manual curation | 2-3x | Low | At design time |
| System prompt | Manual editing | 1.5x | Low | At design time |

### Context Rot Mitigation Checklist

- [ ] Target <50% of advertised window
- [ ] Monitor accuracy by context length
- [ ] Place key info at position 1 or last
- [ ] Prune RAG to top 5 docs (not 20)
- [ ] Rerank: best doc first, second-best last
- [ ] Use hierarchical RAG for complex queries
- [ ] Refresh retrieved docs every N turns
- [ ] Log accuracy metrics by token count

### Prompt Injection Defense Stack

| Layer | Tool | What It Does | When to Apply |
|---|---|---|---|
| 1. Input screening | Prompt Shields, LlamaFirewall | Detect malicious instructions | Every user input |
| 2. Input screening (RAG) | Prompt Shields | Scan retrieved docs | Every retrieved document |
| 3. Delimiters | Microsoft Spotlighting | Mark untrusted content | RAG docs, user uploads |
| 4. Output screening | Schema validator | Validate tool calls | Before execution |
| 5. Action screening | HITL approval | Require human approval | High-risk actions |
| 6. Observability | Span-level logging | Audit all LLM calls | Always |

### Multi-Tenant Isolation Patterns

| Pattern | When to Use | Pros | Cons |
|---|---|---|---|
| **Silo** | Healthcare, finance, enterprise | Complete isolation | High cost, complex ops |
| **Pool** | SMB, low-sensitivity, cost-focused | Low cost, simple | Filter must be perfect |
| **Bridge** | Mixed: premium + standard tiers | Flexible pricing | Complex to manage |

### Token Budget Allocation (Conversational Agent)

| Tier | Budget % | Example (200K total) | Cacheability |
|---|---|---|---|
| System + tools | 10% | 20K | High (1-hour TTL) |
| Retrieval / RAG | 20% | 40K | Medium (5-min TTL) |
| Persistent memory | 30% | 60K | Medium (session TTL) |
| Conversation history | 40% | 80K | Low (growing) |

### Cache Warming Cron Schedule

```
# Warm common prefixes every 4 minutes (before 5-min TTL expires)
*/4 * * * * curl -X POST /api/warm-cache --data '{"prompt_key": "support-agent-v1"}'

# Warm before peak traffic (8:55 AM daily)
55 8 * * * curl -X POST /api/warm-cache --data '{"prompt_key": "support-agent-v1"}'

# Hourly warm for 1-hour TTL (5 minutes before expiry)
55 * * * * curl -X POST /api/warm-cache --data '{"prompt_key": "enterprise-agent-v2"}'
```

### Provider-Specific Gotchas

| Provider | Gotcha | Mitigation |
|---|---|---|
| **Anthropic** | Cache not visible to parallel requests | Serialize warm request |
| **Anthropic** | Longer TTL must come before shorter | Order breakpoints correctly |
| **Anthropic** | Adding tool invalidates all prompts | Declare tools upfront |
| **OpenAI** | Developer message replaces system | Don't mix developer + system |
| **OpenAI** | Cached tokens count toward TPM | Budget for TPM, not just RPM |
| **OpenAI Responses** | Instructions not carried by prev ID | Repeat instructions every call |
| **Gemini** | Idle cache storage costs | Delete cache when not in use |
| **All** | Advertised ≠ effective context | Target <50% of advertised |

### Cost Optimization Priority Order

1. **Prompt caching** (80%+ savings potential)
2. **Semantic caching** (20-60% additional)
3. **Context compression** (50-70% input reduction)
4. **Context-aware routing** (50-90% cost reduction)
5. **Trimming old messages** (10-30% reduction)
6. **Batch API** (50% discount on OpenAI)
7. **Smaller model for simple tasks** (10-100x cheaper)

### Monitoring Dashboard (Key Metrics)

```
Cache Hit Rate:        [====75%====]     Target: >60%
Avg Context Length:    [==45K==]         Target: <50% of limit
Budget Utilization:    [===65%===]       Target: 50-70%
P95 Assembly Latency:  [=78ms=]          Target: <100ms
Cost per Conversation: [$0.12]           Trend: ↓
Accuracy (80K tokens): [==89%==]         Baseline: 92% (watch for drift)
```

### When to Use Each Memory Tier

| Question | Hot (In-Context) | Warm (Redis) | Cold (Vector) |
|---|---|---|---|
| Needed this turn? | Yes | No | No |
| Needed every turn? | Yes | Maybe | No |
| Needed rarely? | No | No | Yes |
| >10K tokens? | No (compress) | Yes | Yes |
| Conversational? | Yes | Yes | No |
| Historical fact? | No | Maybe | Yes |

### Troubleshooting Guide

| Symptom | Likely Cause | Fix |
|---|---|---|---|
| Cache hit rate dropped | Prefix invalidation | Audit recent changes, revert |
| Context overflow error | Budget exceeded | Trim, compress, or summarize |
| Accuracy degraded | Context rot | Reduce window, prune docs |
| High latency | Large prefill | Cache more, compress RAG |
| High cost | No caching | Add breakpoints, enable caching |
| Wrong answers | Lost-in-the-middle | Move key info to top or bottom |
| Inconsistent token counts | Encoding mismatch | Use provider tokenizer, add margin |
| Injection attack | Unvalidated RAG | Add Prompt Shields, use Spotlighting |
