"""
Context Engineering: Token Counting, Budget Allocation, Trimming, and Prompt Assembly.

Context engineering decides what goes into the LLM's context window, in what order,
and at what cost. 65% of enterprise AI failures trace to context drift or memory loss.
"""

import math
import re
from dataclasses import dataclass, field


# ======================================================================
# 1. Token Counting (Simulated tiktoken)
# ======================================================================

def estimate_tokens(text):
    """Estimate token count without tiktoken installed.

    Rule of thumb: 1 token ~ 4 chars in English, ~0.75 words.
    Key gotcha: local estimates diverge 10-20% from API counts due to
    tool call overhead. Always keep a 10-15% safety margin (20% non-English).
    """
    words = re.findall(r"\w+|[^\w\s]", text)
    return max(1, int(len(words) * 1.3))


def demonstrate_token_counting():
    """Show how different content types produce different token counts."""
    examples = {
        "English": "The quick brown fox jumps over the lazy dog.",
        "Code":    "def fibonacci(n): return n if n <= 1 else fibonacci(n-1) + fibonacci(n-2)",
        "JSON":    '{"name": "Alice", "age": 30, "city": "San Francisco", "active": true}',
    }
    for label, text in examples.items():
        print(f"  {label:<10} | {text[:45]:<45} | ~{estimate_tokens(text)} tokens")


# ======================================================================
# 2. Context Window Budget Allocator
# ======================================================================

def allocate_budget(total_tokens, system_pct=0.10, retrieval_pct=0.20,
                    memory_pct=0.30, ephemeral_pct=0.40):
    """Divide context window across the four layers.

    Production allocation: 10% system, 20% retrieval, 30% persistent
    memory, 40% ephemeral. Stable content goes first for cache reuse.
    """
    effective = int(total_tokens * 0.85)  # 15% safety margin
    alloc = {
        "System (tools, role, few-shot)":  int(total_tokens * system_pct),
        "Retrieval (RAG docs, API data)":  int(total_tokens * retrieval_pct),
        "Persistent memory (user prefs)":  int(total_tokens * memory_pct),
        "Ephemeral (chat history, tools)": int(total_tokens * ephemeral_pct),
    }
    cacheability = ["HIGH", "MEDIUM", "MEDIUM", "LOW"]
    print(f"  Total: {total_tokens:,} tokens | Effective (85%): {effective:,}")
    for (label, budget), cache in zip(alloc.items(), cacheability):
        print(f"  {label:<38} {budget:>8,} tok   Cache: {cache}")
    return alloc


# ======================================================================
# 3. Conversation History Trimming Strategies
# ======================================================================

@dataclass
class Message:
    role: str
    content: str
    tokens: int = 0

    def __post_init__(self):
        if self.tokens == 0:
            self.tokens = estimate_tokens(self.content)


def sliding_window_trim(messages, max_tokens, preserve_system=True):
    """Keep most recent messages that fit within budget (lossless).

    Always preserves system messages. Interview tip: use start_on="human"
    to avoid starting mid-conversation.
    """
    system = [m for m in messages if m.role == "system"] if preserve_system else []
    rest = [m for m in messages if m.role != "system"]
    used = sum(m.tokens for m in system)
    kept = []
    for msg in reversed(rest):
        if used + msg.tokens <= max_tokens:
            kept.insert(0, msg)
            used += msg.tokens
        else:
            break
    return system + kept


def summarize_and_trim(messages, summary_threshold=5):
    """Replace old messages with a summary (lossy but space-efficient).

    Risk: abstractive summaries can hallucinate constraints or drop
    negations ("NOT allowed" -> "allowed"). Use extractive for critical facts.
    """
    if len(messages) <= summary_threshold:
        return messages
    split = len(messages) // 2
    old_text = " | ".join(f"[{m.role}] {m.content[:40]}" for m in messages[:split])
    summary = Message("system", f"Summary ({split} msgs): {old_text[:180]}...")
    return [summary] + messages[split:]


# ======================================================================
# 4. Four-Layer Prompt Assembly
# ======================================================================

def assemble_prompt(tool_schemas="", system_prompt="", few_shots=None,
                    memory="", rag_context="", history=None, user_query=""):
    """Build prompt in cache-optimal order.

    Order matters for cache hit rate:
    1. Tool schemas (rarely change) -> highest cache priority
    2. System + few-shot (stable across sessions)
    3. Memory (slow-changing) -> own cache breakpoint
    4. RAG (daily-changing) -> separate breakpoint
    5. History (grows every turn) -> auto-cached
    6. User query (never cached) -> always last
    """
    sections = []
    if tool_schemas:
        sections.append(("TOOLS [cache: 1hr]", tool_schemas))
    if system_prompt:
        sections.append(("SYSTEM [cache: 1hr]", system_prompt))
    if few_shots:
        shots = "\n".join(f"  Ex {i+1}: {s}" for i, s in enumerate(few_shots))
        sections.append(("FEW-SHOT [cache: 1hr]", shots))
    if memory:
        sections.append(("MEMORY [cache: 5min]", memory))
    if rag_context:
        sections.append(("RAG [cache: 5min]", rag_context))
    if history:
        conv = "\n".join(f"  [{m.role}]: {m.content}" for m in history)
        sections.append(("HISTORY [auto-cache]", conv))
    if user_query:
        sections.append(("QUERY [never cached]", user_query))

    total = 0
    for label, content in sections:
        t = estimate_tokens(content)
        total += t
        print(f"  {label:<30} {t:>6} tokens")
    print(f"  {'TOTAL':<30} {total:>6} tokens")
    return sections


# ======================================================================
# 5. Few-Shot Example Selection (Similarity-Based)
# ======================================================================

def cosine_similarity(a, b):
    """Cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def select_few_shots(query_emb, examples, k=3):
    """Pick k most similar few-shot examples for the query.

    In production, use a vector DB (Pinecone, Weaviate). Key insight:
    3-10 high-quality diverse shots beat 100 redundant ones that push
    the query into the lost-in-the-middle zone (>30% accuracy drop).
    """
    scored = [(cosine_similarity(query_emb, ex["emb"]), ex) for ex in examples]
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[:k]


# ======================================================================
# Main: Demo All Snippets
# ======================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("1. TOKEN COUNTING")
    print("=" * 60)
    demonstrate_token_counting()
    print()

    print("=" * 60)
    print("2. CONTEXT BUDGET ALLOCATOR")
    print("=" * 60)
    allocate_budget(200_000)
    print()

    print("=" * 60)
    print("3. CONVERSATION TRIMMING")
    print("=" * 60)
    history = [
        Message("system", "You are a helpful financial analyst."),
        Message("user", "What was Apple's revenue in Q3 2025?"),
        Message("assistant", "Apple reported $94.8B revenue in Q3 2025."),
        Message("user", "How does that compare to Q3 2024?"),
        Message("assistant", "Q3 2024 was $85.8B, so 10.5% YoY growth."),
        Message("user", "What about their services segment?"),
        Message("assistant", "Services hit $24.2B, up 14% YoY."),
        Message("user", "Project Q4 2025 based on this trend."),
    ]
    print(f"  Original: {len(history)} msgs, {sum(m.tokens for m in history)} tokens")
    trimmed = sliding_window_trim(history, max_tokens=50)
    print(f"  Sliding window (50 tok): {len(trimmed)} msgs")
    for m in trimmed:
        print(f"    [{m.role}] {m.content[:55]}")
    summarized = summarize_and_trim(history, summary_threshold=4)
    print(f"  Summarize-and-trim: {len(summarized)} msgs")
    for m in summarized:
        print(f"    [{m.role}] {m.content[:65]}")
    print()

    print("=" * 60)
    print("4. FOUR-LAYER PROMPT ASSEMBLY")
    print("=" * 60)
    assemble_prompt(
        tool_schemas='[{"name": "get_stock_price", "params": {"ticker": "str"}}]',
        system_prompt="You are a financial analyst. Cite sources. Conservative estimates.",
        few_shots=["Q: Revenue of MSFT? A: $211.9B FY2024.", "Q: PE of AAPL? A: 28.5x."],
        memory="User prefers conservative estimates. Last session: AAPL Q3.",
        rag_context="AAPL 10-Q: Total net revenue $94.8B for Q3 2025...",
        history=history[:3],
        user_query="What is Apple's forward PE ratio?",
    )
    print()

    print("=" * 60)
    print("5. FEW-SHOT EXAMPLE SELECTION")
    print("=" * 60)
    examples = [
        {"text": "Calculate PE ratio",      "emb": [0.9, 0.1, 0.2]},
        {"text": "Summarize earnings call", "emb": [0.1, 0.8, 0.3]},
        {"text": "Compare revenue growth",  "emb": [0.7, 0.2, 0.5]},
        {"text": "Explain stock split",     "emb": [0.3, 0.1, 0.9]},
        {"text": "Analyze profit margins",  "emb": [0.8, 0.3, 0.4]},
    ]
    selected = select_few_shots([0.85, 0.15, 0.3], examples, k=3)
    print("  Query: financial ratio question")
    for sim, ex in selected:
        print(f"    sim={sim:.3f}  '{ex['text']}'")
    print("  Tip: Best example at position 1 (top) AND last (recency bias).")
    print("  Middle positions lose >30% accuracy (lost-in-the-middle).")
