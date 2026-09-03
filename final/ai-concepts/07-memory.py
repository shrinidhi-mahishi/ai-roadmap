"""
Agent Memory: LLMs are stateless -- every API call starts fresh. Memory systems bridge this
gap with short-term memory (current conversation in the context window) and long-term memory
(facts/experiences stored externally). The CoALA framework organizes this into working,
semantic, episodic, and procedural memory, each with distinct read/write paths.
"""

import math
import time
from dataclasses import dataclass, field

# ═══ Conversation Buffer Memory (Sliding Window) ═══
# The simplest STM: keep recent messages in a FIFO buffer with a token budget.
# Token-budgeted is strictly better than fixed-k because message sizes vary.

@dataclass
class Message:
    role: str
    content: str
    tokens: int = 0

    def __post_init__(self):
        self.tokens = self.tokens or max(1, len(self.content) // 4)


class ConversationBufferMemory:
    """Sliding window memory with token budget. Drops oldest messages first.
    Mirrors LangChain's trim_messages(strategy='last', max_tokens=N)."""

    def __init__(self, max_tokens: int = 200, always_keep_system: bool = True):
        self.max_tokens = max_tokens
        self.always_keep_system = always_keep_system
        self.messages: list[Message] = []

    def add(self, role: str, content: str):
        self.messages.append(Message(role=role, content=content))
        self._trim()

    def _trim(self):
        """Drop oldest messages until under budget, keeping system prompt."""
        while self._total_tokens() > self.max_tokens and len(self.messages) > 1:
            if self.always_keep_system and self.messages[0].role == "system":
                if len(self.messages) > 2:
                    self.messages.pop(1)
                else:
                    break
            else:
                self.messages.pop(0)

    def _total_tokens(self) -> int:
        return sum(m.tokens for m in self.messages)

    def get_context(self) -> list[dict]:
        return [{"role": m.role, "content": m.content} for m in self.messages]


def demo_buffer_memory():
    mem = ConversationBufferMemory(max_tokens=60)
    mem.add("system", "You are a helpful assistant.")
    mem.add("user", "I am vegetarian.")  # early constraint -- will it survive?
    mem.add("user", "What should I eat for dinner tonight?")
    mem.add("assistant", "Here are some great vegetarian dinner ideas for you.")
    mem.add("user", "Can you suggest something Italian?")
    print(f"  Token budget: {mem.max_tokens}, Current: {mem._total_tokens()}")
    for msg in mem.get_context():
        print(f"  [{msg['role']}] {msg['content'][:60]}")


# ═══ Semantic Memory Store ═══
# Durable facts about the user/world. Answers "what is true NOW?"
# Write: extract facts from conversation. Read: embed + retrieve by similarity.

def fake_embed(text: str, dim: int = 8) -> list[float]:
    """Deterministic pseudo-embedding for demo purposes."""
    vals = [0.0] * dim
    for i, ch in enumerate(text.lower()):
        vals[i % dim] += ord(ch) * 0.001
    norm = math.sqrt(sum(v * v for v in vals)) or 1.0
    return [v / norm for v in vals]

def cosine_sim(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x*x for x in a)), math.sqrt(sum(x*x for x in b))
    return dot / (na * nb) if na and nb else 0.0

@dataclass
class SemanticFact:
    text: str
    embedding: list[float] = field(default_factory=list)
    source: str = "conversation"  # origin tag for poisoning defense

    def __post_init__(self):
        if not self.embedding:
            self.embedding = fake_embed(self.text)


class SemanticMemoryStore:
    """Vector-backed fact store. Similar to Mem0 or LangGraph Store."""
    def __init__(self):
        self.facts: list[SemanticFact] = []

    def add(self, text: str, source: str = "conversation"):
        self.facts.append(SemanticFact(text=text, source=source))

    def search(self, query: str, top_k: int = 3) -> list[tuple[str, float]]:
        q_vec = fake_embed(query)
        scored = [(f.text, cosine_sim(q_vec, f.embedding)) for f in self.facts]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]


def demo_semantic_memory():
    store = SemanticMemoryStore()
    store.add("User is vegetarian since 2024")
    store.add("User works at Acme Corp as a data scientist")
    store.add("User prefers Python over Java")
    store.add("User lives in Bangalore, India")
    results = store.search("What does the user eat?")
    print("  Query: 'What does the user eat?'")
    for text, score in results:
        print(f"    [{score:.3f}] {text}")


# ═══ Episodic Memory ═══
# Records WHAT HAPPENED, not just facts. Needed for audit, unlearning, and citation.
# Scoring uses recency + importance + relevance (Generative Agents formula).

@dataclass
class Episode:
    description: str
    importance: int = 5  # LLM-rated 1-10 at write time
    embedding: list[float] = field(default_factory=list)
    last_accessed: float = field(default_factory=time.time)

    def __post_init__(self):
        if not self.embedding:
            self.embedding = fake_embed(self.description)


class EpisodicMemory:
    """Stores experiences with timestamps. Retrieval blends recency, importance,
    and relevance -- the Generative Agents formula (Park et al., UIST 2023)."""
    def __init__(self):
        self.episodes: list[Episode] = []

    def record(self, description: str, importance: int = 5):
        self.episodes.append(Episode(description=description, importance=importance))

    def retrieve(self, query: str, top_k: int = 3) -> list[tuple[str, float]]:
        """Three-signal scoring: recency + importance + relevance."""
        now = time.time()
        q_vec = fake_embed(query)
        scored = []
        for ep in self.episodes:
            hours = (now - ep.last_accessed) / 3600
            recency = 0.995 ** hours          # exponential decay
            importance = ep.importance / 10.0  # normalize to [0,1]
            relevance = cosine_sim(q_vec, ep.embedding)
            scored.append((ep.description, recency + importance + relevance))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]


def demo_episodic_memory():
    mem = EpisodicMemory()
    mem.record("User complained about order #123 being late", importance=7)
    mem.record("User asked about return policy", importance=4)
    mem.record("User upgraded to premium plan", importance=9)
    mem.record("User mentioned they prefer email over chat", importance=3)
    results = mem.retrieve("previous issues with orders")
    print("  Query: 'previous issues with orders'")
    for desc, score in results:
        print(f"    [{score:.3f}] {desc}")


# ═══ Memory Consolidation ═══
# Sleep-time compute: shift work from query time to background processing.
# Summarize old memories, merge duplicates, update stale facts.

class MemoryConsolidator:
    """Merges and summarizes memories to reduce retrieval noise and token cost.
    Mirrors Letta sleep-time agents and OpenAI Dreaming V3."""

    @staticmethod
    def summarize_group(memories: list[str]) -> str:
        """Compress related memories. Real: call LLM. Here we simulate."""
        if len(memories) == 1:
            return memories[0]
        parts = [m.split(".")[0] for m in memories]
        return "Summary: " + "; ".join(parts)

    @staticmethod
    def word_overlap(a: str, b: str) -> float:
        """Jaccard similarity on words -- reliable for dedup."""
        wa, wb = set(a.lower().split()), set(b.lower().split())
        return len(wa & wb) / len(wa | wb) if wa | wb else 0.0

    @staticmethod
    def consolidate(store: SemanticMemoryStore, threshold: float = 0.3) -> SemanticMemoryStore:
        """Merge duplicates and summarize clusters. Run async, never block TTFT."""
        dupes = []
        for i in range(len(store.facts)):
            for j in range(i + 1, len(store.facts)):
                if MemoryConsolidator.word_overlap(store.facts[i].text, store.facts[j].text) > threshold:
                    dupes.append((i, j))
        if not dupes:
            return store
        merged_indices = set()
        new_store = SemanticMemoryStore()
        for i, j in dupes:
            merged = MemoryConsolidator.summarize_group([store.facts[i].text, store.facts[j].text])
            new_store.add(merged, source="consolidation")
            merged_indices.update([i, j])
        for i, fact in enumerate(store.facts):
            if i not in merged_indices:
                new_store.add(fact.text, source=fact.source)
        return new_store


def demo_consolidation():
    store = SemanticMemoryStore()
    store.add("User is vegetarian")
    store.add("User follows a vegetarian diet")  # near-duplicate
    store.add("User works at Acme Corp")
    store.add("User prefers Python")
    print(f"  Before: {len(store.facts)} facts")
    consolidated = MemoryConsolidator.consolidate(store)
    print(f"  After consolidation: {len(consolidated.facts)} facts")
    for fact in consolidated.facts:
        print(f"    [{fact.source}] {fact.text}")


# ═══ CoALA-Style Memory System ═══
# Full architecture: STM (working memory) + LTM (semantic + episodic) with
# explicit read/write paths. The LLM is NOT the memory -- it emits tool calls.

class CoALAMemorySystem:
    """Cognitive architecture: working memory (prompt) + long-term memory (store).
    Read path: query -> retrieve -> rerank -> inject into prompt.
    Write path: experience -> extract facts -> entity-resolve -> store."""

    def __init__(self, stm_budget: int = 150):
        self.stm = ConversationBufferMemory(max_tokens=stm_budget)
        self.semantic = SemanticMemoryStore()
        self.episodic = EpisodicMemory()

    def write_semantic(self, text: str):
        self.semantic.add(text)

    def write_episodic(self, description: str, importance: int = 5):
        self.episodic.record(description, importance)

    def read_memories(self, query: str, max_facts: int = 2, max_episodes: int = 2) -> str:
        """Read-path: retrieve relevant facts + episodes, format for injection."""
        facts = self.semantic.search(query, top_k=max_facts)
        episodes = self.episodic.retrieve(query, top_k=max_episodes)
        lines = []
        if facts:
            lines.append("Known facts:")
            lines.extend(f"  - {text}" for text, _ in facts)
        if episodes:
            lines.append("Past experiences:")
            lines.extend(f"  - {desc}" for desc, _ in episodes)
        return "\n".join(lines) if lines else "(no relevant memories)"

    def build_prompt(self, user_message: str) -> list[dict]:
        """Assemble the full working memory prompt: system + memories + history."""
        memories = self.read_memories(user_message)
        system = f"You are a helpful assistant.\n\nMemory context:\n{memories}"
        self.stm.add("user", user_message)
        context = [{"role": "system", "content": system}]
        context.extend(self.stm.get_context())
        return context


def demo_coala():
    system = CoALAMemorySystem(stm_budget=100)
    system.write_semantic("User is vegetarian since 2024")
    system.write_semantic("User prefers Italian cuisine")
    system.write_episodic("User ordered pasta last week and loved it", importance=7)
    system.write_episodic("User had a billing issue on June 5", importance=8)
    prompt = system.build_prompt("What should I eat tonight?")
    print("  Assembled prompt:")
    for msg in prompt:
        content_preview = msg["content"][:80].replace("\n", " | ")
        print(f"    [{msg['role']}] {content_preview}...")


# ═══ Run All Demos ═══

if __name__ == "__main__":
    print("=" * 60)
    print("1. Conversation Buffer Memory (Sliding Window)")
    print("=" * 60)
    demo_buffer_memory()

    print("\n" + "=" * 60)
    print("2. Semantic Memory Store (Facts + Similarity Search)")
    print("=" * 60)
    demo_semantic_memory()

    print("\n" + "=" * 60)
    print("3. Episodic Memory (Recency + Importance + Relevance)")
    print("=" * 60)
    demo_episodic_memory()

    print("\n" + "=" * 60)
    print("4. Memory Consolidation (Summarize + Merge)")
    print("=" * 60)
    demo_consolidation()

    print("\n" + "=" * 60)
    print("5. CoALA-Style Full Memory System (STM + LTM)")
    print("=" * 60)
    demo_coala()
