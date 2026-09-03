"""
LLM Foundations: Tokenization, Attention, Sampling, and Positional Encoding.

Core building blocks of every large language model -- understanding these primitives
is essential for architecture discussions, cost estimation, and debugging inference
behavior in production systems.
"""

import math
import numpy as np
from collections import Counter


# ======================================================================
# 1. BPE Tokenization (Byte Pair Encoding)
# ======================================================================

def bpe_tokenize(text, num_merges=10):
    """Simplified BPE: iteratively merge the most frequent adjacent pair.

    Real tokenizers (tiktoken, sentencepiece) work on bytes and pre-split by
    regex, but the merge loop is identical. This demo shows WHY "lowest" might
    tokenize as ["low", "est"] -- the training corpus determined which merges
    were learned.
    """
    # Start with character-level tokens (real BPE starts with bytes)
    tokens = list(text)

    for step in range(num_merges):
        # Count adjacent pairs
        pairs = Counter()
        for i in range(len(tokens) - 1):
            pairs[(tokens[i], tokens[i + 1])] += 1

        if not pairs:
            break

        # Merge the most frequent pair
        best_pair = pairs.most_common(1)[0]
        pair, freq = best_pair
        if freq < 2:
            break  # No pair repeats -- stop early

        merged = pair[0] + pair[1]
        new_tokens = []
        i = 0
        while i < len(tokens):
            if i < len(tokens) - 1 and tokens[i] == pair[0] and tokens[i + 1] == pair[1]:
                new_tokens.append(merged)
                i += 2
            else:
                new_tokens.append(tokens[i])
                i += 1
        tokens = new_tokens
        print(f"  Step {step + 1}: merge '{pair[0]}' + '{pair[1]}' -> '{merged}'  (freq={freq})  tokens={tokens}")

    return tokens


# ======================================================================
# 2. Simplified Self-Attention (Q, K, V)
# ======================================================================

def self_attention(embeddings, d_k=None):
    """Single-head self-attention on a sequence of embedding vectors.

    Shows the core formula: Attention(Q,K,V) = softmax(QK^T / sqrt(d_k)) V
    In real transformers, Q/K/V come from learned linear projections; here we
    use the raw embeddings for clarity.
    """
    # embeddings shape: (seq_len, d_model)
    seq_len, d_model = embeddings.shape
    if d_k is None:
        d_k = d_model

    # In practice these are learned projections: Q = X @ W_q, etc.
    Q = embeddings  # (seq_len, d_k)
    K = embeddings
    V = embeddings

    # Scaled dot-product attention scores
    scores = Q @ K.T / math.sqrt(d_k)  # (seq_len, seq_len)

    # Softmax along the last axis (each row sums to 1)
    exp_scores = np.exp(scores - scores.max(axis=-1, keepdims=True))
    attn_weights = exp_scores / exp_scores.sum(axis=-1, keepdims=True)

    # Weighted sum of values
    output = attn_weights @ V  # (seq_len, d_model)
    return attn_weights, output


# ======================================================================
# 3. Temperature Sampling
# ======================================================================

def temperature_sampling(logits, temperature=1.0):
    """Show how temperature reshapes the probability distribution.

    Low temperature -> peaky (deterministic), high -> flat (creative).
    Temperature 0 is greedy (argmax). Interview tip: temperature 0 is NOT
    truly deterministic due to GPU floating-point non-determinism.
    """
    if temperature == 0:
        probs = np.zeros_like(logits, dtype=float)
        probs[np.argmax(logits)] = 1.0
        return probs

    scaled = logits / temperature
    exp_scaled = np.exp(scaled - scaled.max())  # Subtract max for numerical stability
    probs = exp_scaled / exp_scaled.sum()
    return probs


# ======================================================================
# 4. Top-k and Top-p (Nucleus) Sampling
# ======================================================================

def top_k_sampling(probs, k):
    """Keep only the top-k most probable tokens, zero out the rest, renormalize."""
    sorted_indices = np.argsort(probs)[::-1]
    filtered = np.zeros_like(probs)
    for idx in sorted_indices[:k]:
        filtered[idx] = probs[idx]
    return filtered / filtered.sum()


def top_p_sampling(probs, p):
    """Nucleus sampling: keep smallest set of tokens whose cumulative prob >= p.

    This adapts the candidate pool size dynamically -- when the model is confident,
    fewer tokens pass; when uncertain, more tokens pass. This is why top-p is
    generally preferred over top-k in production.
    """
    sorted_indices = np.argsort(probs)[::-1]
    sorted_probs = probs[sorted_indices]
    cumulative = np.cumsum(sorted_probs)

    # Find cutoff: first index where cumulative >= p
    cutoff = np.searchsorted(cumulative, p) + 1

    filtered = np.zeros_like(probs)
    for idx in sorted_indices[:cutoff]:
        filtered[idx] = probs[idx]
    return filtered / filtered.sum()


# ======================================================================
# 5. Sinusoidal Positional Encoding
# ======================================================================

def sinusoidal_positional_encoding(seq_len, d_model):
    """Generate the original Transformer positional encoding matrix.

    PE(pos, 2i)   = sin(pos / 10000^(2i/d_model))
    PE(pos, 2i+1) = cos(pos / 10000^(2i/d_model))

    Key insight: the wavelength grows geometrically across dimensions, so nearby
    positions have similar encodings (good for local attention) while distant
    positions are distinguishable. RoPE (used by Llama, most 2026 models) applies
    these rotations directly to Q and K vectors instead of adding to embeddings.
    """
    pe = np.zeros((seq_len, d_model))
    position = np.arange(seq_len).reshape(-1, 1)
    div_term = np.exp(np.arange(0, d_model, 2) * -(math.log(10000.0) / d_model))

    pe[:, 0::2] = np.sin(position * div_term)  # Even indices
    pe[:, 1::2] = np.cos(position * div_term)  # Odd indices
    return pe


# ======================================================================
# Main: Demo All Snippets
# ======================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("1. BPE TOKENIZATION")
    print("=" * 60)
    text = "low lower lowest"
    print(f"Input: '{text}'")
    tokens = bpe_tokenize(text, num_merges=12)
    print(f"Final tokens: {tokens}\n")

    print("=" * 60)
    print("2. SELF-ATTENTION")
    print("=" * 60)
    # 4 tokens, 3-dim embeddings (toy example)
    words = ["The", "cat", "sat", "down"]
    embeddings = np.array([
        [1.0, 0.0, 1.0],   # "The"
        [0.0, 1.0, 0.0],   # "cat"
        [1.0, 1.0, 0.0],   # "sat"
        [0.0, 0.0, 1.0],   # "down"
    ])
    weights, output = self_attention(embeddings)
    print("Attention weights (each row = how much token i attends to token j):")
    for i, word in enumerate(words):
        row = "  ".join(f"{w:.3f}" for w in weights[i])
        print(f"  {word:>4s} -> [{row}]")
    print()

    print("=" * 60)
    print("3. TEMPERATURE SAMPLING")
    print("=" * 60)
    vocab = ["Paris", "London", "Berlin", "Tokyo", "Madrid"]
    logits = np.array([5.0, 2.0, 1.5, 1.0, 0.5])
    for temp in [0.0, 0.3, 1.0, 2.0]:
        probs = temperature_sampling(logits, temp)
        dist = "  ".join(f"{vocab[i]}={probs[i]:.3f}" for i in range(len(vocab)))
        print(f"  T={temp:.1f}: {dist}")
    print()

    print("=" * 60)
    print("4. TOP-K AND TOP-P SAMPLING")
    print("=" * 60)
    base_probs = temperature_sampling(logits, temperature=1.0)
    print(f"  Base probs:  {['%.3f' % p for p in base_probs]}")
    tk = top_k_sampling(base_probs, k=3)
    print(f"  Top-k (k=3): {['%.3f' % p for p in tk]}")
    tp = top_p_sampling(base_probs, p=0.9)
    print(f"  Top-p (p=0.9): {['%.3f' % p for p in tp]}")
    print()

    print("=" * 60)
    print("5. SINUSOIDAL POSITIONAL ENCODING")
    print("=" * 60)
    pe = sinusoidal_positional_encoding(seq_len=8, d_model=6)
    print("PE matrix (8 positions x 6 dimensions):")
    print("  Pos  |  " + "  ".join(f"d{i}" for i in range(6)))
    print("  " + "-" * 50)
    for pos in range(8):
        vals = "  ".join(f"{pe[pos, d]:+.3f}" for d in range(6))
        print(f"  {pos:>3d}  |  {vals}")
    print("\nNotice: low-index dims oscillate fast (local), high-index dims oscillate slow (global).")
