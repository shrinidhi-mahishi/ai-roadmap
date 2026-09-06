"""Prompt Engineering Patterns -- core patterns for interview prep.

Covers the 6-layer context assembly pipeline, system prompt templates, few-shot
prompting with pinned order, chain-of-thought patterns, structured output with
decoder constraints, prompt versioning and A/B testing, and the artifact pin
invariant. Prompt engineering is context engineering -- disciplined assembly of
instructions, examples, schemas, and constraints.
"""

import hashlib
import json
import random
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional


# ============================================================================
# --- Section 1: System Prompt Template Patterns ---
# ============================================================================
# The system prompt is the developer's control plane. It sets persona, policies,
# constraints, and output format. It lives in the LEFT prefix for cache efficiency.
# NEVER put timestamps, user IDs, or {{now}} in the system prompt -- that busts
# the entire prefix cache.

SYSTEM_PROMPT_TEMPLATE = """You are {persona}.

## Role
{role_description}

## Constraints
{constraints}

## Output Format
{output_format}

## Examples
{examples_block}
"""

# Cache-friendly design: stable content in LEFT prefix, volatile in RIGHT suffix
# Anthropic: cache read = 0.1x input cost, write = 1.25x. 5-min TTL.
# A timestamp in the system prompt busts the ENTIRE cache.


def build_system_prompt(
    persona: str,
    role_description: str,
    constraints: list[str],
    output_format: str,
    examples: list[dict] = None,
) -> str:
    """Build a system prompt from structured components.

    Design rules for cache efficiency:
    1. Static content (persona, constraints, format) goes in system prompt (cacheable prefix)
    2. Dynamic content (user query, retrieved docs) goes in user message (volatile suffix)
    3. Never put timestamps, session IDs, or per-request identifiers here
    4. Changing output_config.format on Anthropic invalidates the thread's prompt cache
    """
    constraints_text = "\n".join(f"- {c}" for c in constraints)

    examples_block = ""
    if examples:
        parts = []
        for ex in examples:
            parts.append(f"Input: {ex['input']}\nOutput: {ex['output']}")
        examples_block = "\n---\n".join(parts)

    return SYSTEM_PROMPT_TEMPLATE.format(
        persona=persona,
        role_description=role_description,
        constraints=constraints_text,
        output_format=output_format,
        examples_block=examples_block or "(See few-shot examples below)",
    )


# Example: Invoice extraction system prompt
INVOICE_SYSTEM_PROMPT = build_system_prompt(
    persona="a precise invoice data extractor",
    role_description="Extract structured data from invoice text. Return only the JSON, no commentary.",
    constraints=[
        "Extract vendor name, invoice number, date, and total amount",
        "If a field is missing, use null (do not guess)",
        "Amounts must be numbers, not strings",
        "Dates in ISO 8601 format (YYYY-MM-DD)",
    ],
    output_format='JSON matching: {"vendor": str, "invoice_number": str|null, "date": str|null, "total": float}',
)


# ============================================================================
# --- Section 2: Few-Shot Prompting with Pinned Order ---
# ============================================================================
# Exemplar order matters ENORMOUSLY. Lu et al.: 4-shot SST-2 permutations
# range from ~50% (chance) to >85% accuracy. Pin the order.
# "We'll just add 5 shots" without controlling order is an interview fail.

@dataclass
class FewShotExample:
    input_text: str
    output_text: str
    rationale: Optional[str] = None  # For CoT examples


class FewShotBank:
    """Static few-shot bank with pinned order.

    Key properties:
    - Order is part of the prompt hash (changing order = new artifact)
    - Examples must be PII-scrubbed before registration
    - Static bank goes in the cacheable prefix
    - kNN-selected examples go in the volatile suffix (cache bust)
    """

    def __init__(self):
        self._examples: list[FewShotExample] = []

    def add(self, input_text: str, output_text: str, rationale: str = None) -> None:
        """Add an example. Order of addition = order of rendering."""
        self._examples.append(FewShotExample(input_text, output_text, rationale))

    @property
    def order_hash(self) -> str:
        """Hash of the example bank in its current order.
        This is part of the prompt artifact ID."""
        blob = json.dumps(
            [{"i": e.input_text, "o": e.output_text} for e in self._examples],
            sort_keys=True,
        )
        return hashlib.sha256(blob.encode()).hexdigest()[:16]

    def render(self, include_rationale: bool = False) -> str:
        """Render examples in pinned order. Include rationale for CoT."""
        parts = []
        for i, ex in enumerate(self._examples, 1):
            lines = [f"Example {i}:", f"Input: {ex.input_text}"]
            if include_rationale and ex.rationale:
                lines.append(f"Reasoning: {ex.rationale}")
            lines.append(f"Output: {ex.output_text}")
            parts.append("\n".join(lines))
        return "\n\n---\n\n".join(parts)

    def render_as_messages(self) -> list[dict]:
        """Render as alternating user/assistant messages for chat models.
        This format works better than text blocks for most chat-tuned models."""
        messages = []
        for ex in self._examples:
            messages.append({"role": "user", "content": ex.input_text})
            content = ex.output_text
            if ex.rationale:
                content = f"Reasoning: {ex.rationale}\n\nAnswer: {ex.output_text}"
            messages.append({"role": "assistant", "content": content})
        return messages


# Four few-shot selection strategies
FEW_SHOT_STRATEGIES = {
    "fixed_bank": {
        "approach": "Static, reviewed, PII-scrubbed examples",
        "cache_impact": "Cacheable prefix (best for cost)",
        "best_for": "Homogeneous tasks with stable input distribution",
    },
    "KATE_similarity": {
        "approach": "kNN on embeddings, most similar examples first",
        "cache_impact": "Volatile suffix (cache bust -- different examples per query)",
        "best_for": "Diverse inputs where relevance of examples matters",
    },
    "MMR_DPP": {
        "approach": "Maximize relevance + diversity (avoid near-duplicate clusters)",
        "cache_impact": "Volatile suffix",
        "best_for": "When example diversity prevents mode collapse",
    },
    "IDS": {
        "approach": "Zero-shot-CoT first, then re-select examples based on output",
        "cache_impact": "Two-stage, volatile",
        "best_for": "Complex reasoning where initial attempt guides example selection",
    },
}


# ============================================================================
# --- Section 3: Chain-of-Thought Prompting ---
# ============================================================================
# CoT for multi-step math/logic: GSM8K 17.9% -> 56.9% (Wei, PaLM 540B).
# CoT can HURT on NLI/extraction (e-SNLI: 85.8 -> 81.0).
# Always measure before adding CoT. Not all tasks benefit.

def zero_shot_cot_prompt(question: str) -> list[dict]:
    """Zero-shot Chain-of-Thought: "Let's think step by step."

    Kojima et al.: MultiArith accuracy 17.7% -> 78.7% with just this suffix.
    Two serial API calls: (1) generate reasoning, (2) extract answer.
    Cost: ~2x output tokens vs zero-shot.
    """
    return [
        {"role": "system", "content": "You are a helpful reasoning assistant."},
        {"role": "user", "content": f"{question}\n\nLet's think step by step."},
    ]


def few_shot_cot_prompt(
    question: str,
    examples: FewShotBank,
    system_prompt: str = "You are a helpful reasoning assistant. Show your work step by step.",
) -> list[dict]:
    """Few-shot Chain-of-Thought with worked examples.

    Key: the quality of rationales in examples matters for CoT (unlike
    classification where random labels only drop ~0-5 abs).
    """
    messages = [{"role": "system", "content": system_prompt}]
    # Add few-shot examples as alternating messages
    messages.extend(examples.render_as_messages())
    # Add the actual question
    messages.append({"role": "user", "content": question})
    return messages


def self_consistency_vote(
    responses: list[str],
    extract_answer: Callable[[str], str],
) -> tuple[str, float]:
    """Self-Consistency (Wang et al.): sample N CoT paths, majority vote.

    Start with N=5 at temperature 0.5-0.7 ("saturates quickly").
    N=40 at Sonnet pricing = ~$780/1k calls -- NEVER a chat default.
    For open-ended tasks, majority vote needs a canonicalizer.

    Returns (winning_answer, confidence as vote fraction).
    """
    answers = [extract_answer(r) for r in responses]
    # Count votes
    counts: dict[str, int] = defaultdict(int)
    for a in answers:
        counts[a] += 1

    winner = max(counts, key=counts.get)
    confidence = counts[winner] / len(answers)
    return winner, confidence


# CoT pattern comparison (interview reference)
COT_PATTERNS = {
    "CoT (Wei 2022)": {
        "result": "GSM8K: 17.9% -> 56.9% (PaLM 540B)",
        "cost": "~2x output tokens",
        "when": "Multi-step math/logic",
    },
    "ZS-CoT (Kojima)": {
        "result": "MultiArith: 17.7 -> 78.7%",
        "cost": "2 serial API calls",
        "when": "Quick reasoning boost, no examples needed",
    },
    "SC (Wang)": {
        "result": "GSM8K: +17.9 over CoT (N=40)",
        "cost": "Nx CoT cost",
        "when": "When accuracy > cost/latency",
    },
    "L2M (Zhou)": {
        "result": "SCAN length-split: 16.2% -> 99.7%",
        "cost": ">=2 sequential calls",
        "when": "Compositional generalization",
    },
    "ToT (Yao)": {
        "result": "Game of 24: 4% -> 74% (b=5, GPT-4)",
        "cost": "5-100x more tokens",
        "when": "Search/planning problems",
    },
}

# Anti-patterns to call out in interviews
COT_ANTI_PATTERNS = [
    "CoT can HURT on NLI/extraction (e-SNLI: 85.8 -> 81.0)",
    "SC N=40 as chat default = ~$780/1k Sonnet calls",
    "CoT on single-step problems: near zero or negative gains",
    "ZS-CoT on commonsense without huge scale: often no help",
]


# ============================================================================
# --- Section 4: Structured Output (JSON Mode / Schema-Constrained) ---
# ============================================================================
# Three layers -- do NOT collapse them:
# 1. Prompt-only ("Reply as JSON") -> no guarantee
# 2. JSON mode (json_object / mime_type) -> valid syntax, no schema guarantee
# 3. Schema-constrained (FSM/PDA mask) -> 100% schema compliance (conditional)

class OutputMode(Enum):
    FREE_TEXT = "free_text"          # Standard sampling, no constraint
    JSON_MODE = "json_mode"          # Valid JSON syntax, no schema guarantee
    SCHEMA_CONSTRAINED = "schema"    # 100% schema compliance (conditional)


@dataclass(frozen=True)
class OutputSchema:
    """Defines the expected output schema for structured output.

    Anthropic limits: max 20 strict tools/request, 24 optional params
    across all strict schemas, 16 anyOf. Compile timeout 180s.
    """
    name: str
    schema: dict    # JSON Schema
    strict: bool = True

    @property
    def schema_hash(self) -> str:
        return "s_" + hashlib.sha256(
            json.dumps(self.schema, sort_keys=True).encode()
        ).hexdigest()[:16]


# Example schema for invoice extraction
INVOICE_SCHEMA = OutputSchema(
    name="invoice_extraction",
    schema={
        "type": "object",
        "properties": {
            "vendor": {"type": "string"},
            "invoice_number": {"type": ["string", "null"]},
            "date": {"type": ["string", "null"], "description": "ISO 8601 date"},
            "total": {"type": "number"},
        },
        "required": ["vendor", "total"],
        "additionalProperties": False,  # Anthropic injects this automatically
    },
)


def build_structured_output_request(
    messages: list[dict],
    schema: OutputSchema,
    mode: OutputMode = OutputMode.SCHEMA_CONSTRAINED,
) -> dict:
    """Build an API request with structured output configuration.

    OpenAI: response_format={"type": "json_schema", "json_schema": {...}}
    Anthropic: output_config={"format": {"type": "json_schema", "json_schema": {...}}}
    Gemini: generationConfig.responseMimeType + responseSchema

    ALWAYS check finish_reason before parsing:
    - "stop" -> safe to parse
    - "length" / "max_tokens" -> truncated, schema may be invalid
    - "content_filter" / refusal -> no valid output
    """
    request = {"messages": messages}

    if mode == OutputMode.SCHEMA_CONSTRAINED:
        # OpenAI format (Anthropic similar but uses output_config)
        request["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": schema.name,
                "strict": schema.strict,
                "schema": schema.schema,
            },
        }
    elif mode == OutputMode.JSON_MODE:
        request["response_format"] = {"type": "json_object"}

    return request


class StructuredOutputParser:
    """Parse structured output with circuit breaker on consecutive failures.

    Fallback chain: schema-constrained -> Instructor retry (cap 1) -> 422.
    Never loop to max_turns on parse failures.
    """

    def __init__(self, max_retries: int = 1, circuit_threshold: int = 3):
        self.max_retries = max_retries
        self._consecutive_failures = 0
        self._circuit_threshold = circuit_threshold

    def parse(self, raw: str, schema: dict, finish_reason: str = "stop") -> dict:
        """Parse with validation. Check finish_reason FIRST."""
        # Circuit breaker: after N consecutive failures, return 422
        if self._consecutive_failures >= self._circuit_threshold:
            raise RuntimeError(
                "circuit_open:parse_failures -- "
                f"{self._consecutive_failures} consecutive failures"
            )

        # Check finish_reason before attempting parse
        if finish_reason in ("length", "max_tokens"):
            self._consecutive_failures += 1
            raise ValueError("truncated_output: finish_reason indicates truncation")
        if finish_reason in ("content_filter", "refusal"):
            self._consecutive_failures += 1
            raise ValueError(f"no_output: finish_reason={finish_reason}")

        try:
            result = json.loads(raw)
            # Validate required fields
            for key in schema.get("required", []):
                if key not in result:
                    raise ValueError(f"missing_required_field: {key}")
            # Validate types (simplified)
            for key, value in result.items():
                if key in schema.get("properties", {}):
                    expected = schema["properties"][key].get("type")
                    if expected == "number" and not isinstance(value, (int, float)):
                        raise ValueError(f"type_error: {key} should be number")

            self._consecutive_failures = 0  # Reset on success
            return result

        except (json.JSONDecodeError, ValueError) as e:
            self._consecutive_failures += 1
            raise ValueError(f"parse_failure: {e}") from e


# ============================================================================
# --- Section 5: Prompt Versioning and A/B Testing ---
# ============================================================================
# Git is SoT. Tags are mutable pointers to immutable hashes.
# Never serve "latest". Rollback = retag previous hash (seconds, not rebuild).
# Artifact pin: (prompt_hash, schema_hash, model_id, decoding_params, optimizer_id, metric_id)

@dataclass(frozen=True)
class PromptArtifact:
    """Immutable prompt artifact. Changing ANY element = new artifact.

    Pin: prompt_hash + schema_hash + model_id + decoding_params + optimizer_id + metric_id
    A schema-valid JSON object is NOT an authorized action.
    Constrained decode guarantees a language, not truth.
    """
    text: str
    schema_hash: str
    model_id: str
    decoding_params: str       # e.g., "t0.7_max800_topP0.95"
    optimizer_id: str = "none"  # DSPy optimizer if used
    metric_id: str = "none"     # Eval metric if used

    @property
    def prompt_hash(self) -> str:
        return "p_" + hashlib.sha256(self.text.encode()).hexdigest()[:16]

    @property
    def artifact_id(self) -> str:
        """Full artifact identity. Changing any element = different artifact."""
        return "|".join([
            self.prompt_hash, self.schema_hash, self.model_id,
            self.decoding_params, self.optimizer_id, self.metric_id,
        ])


class PromptRegistry:
    """Prompt registry with immutable versions and mutable tags.

    Production rules:
    - Git is SoT for prompt text
    - Registry stores immutable versions with hash-addressed commits
    - Tags (staging, production) are mutable pointers
    - Rollback = retag previous hash (seconds, not rebuild)
    - Cache key must include prompt_hash (stale completions survive promote otherwise)
    """

    def __init__(self):
        self._artifacts: dict[str, PromptArtifact] = {}  # hash -> artifact
        self._tags: dict[str, str] = {}                   # tag -> hash
        self._history: list[dict] = []                    # Audit trail

    def register(self, artifact: PromptArtifact) -> str:
        h = artifact.prompt_hash
        self._artifacts[h] = artifact
        return h

    def tag(self, tag_name: str, prompt_hash: str, actor: str) -> None:
        """Point a tag at a specific hash. This is the promotion mechanism."""
        if prompt_hash not in self._artifacts:
            raise KeyError(f"Unknown hash: {prompt_hash}")

        old_hash = self._tags.get(tag_name)
        self._tags[tag_name] = prompt_hash
        self._history.append({
            "action": "tag",
            "tag": tag_name,
            "old_hash": old_hash,
            "new_hash": prompt_hash,
            "actor": actor,
            "timestamp": time.time(),
        })

    def resolve(self, tag_name: str) -> PromptArtifact:
        """Resolve a tag to its artifact. Never serve 'latest' directly."""
        h = self._tags.get(tag_name)
        if not h or h not in self._artifacts:
            raise KeyError(f"Tag not found or dangling: {tag_name}")
        return self._artifacts[h]

    def rollback(self, tag_name: str, actor: str) -> str:
        """Rollback to previous hash. Instant -- no rebuild needed."""
        # Find the previous hash for this tag
        tag_history = [
            e for e in reversed(self._history)
            if e["tag"] == tag_name and e["old_hash"] is not None
        ]
        if not tag_history:
            raise ValueError(f"No rollback history for tag: {tag_name}")

        prev_hash = tag_history[0]["old_hash"]
        self.tag(tag_name, prev_hash, actor=f"rollback:{actor}")
        return prev_hash


class ABTest:
    """Simple A/B test for prompt variants using sticky assignment.

    Key design:
    - Use sticky thread_id for consistent user experience
    - 5% canary rollout before full promotion
    - Feature flag kill = instant rollback (seconds)
    """

    def __init__(
        self,
        control_hash: str,
        variant_hash: str,
        variant_pct: float = 0.05,  # 5% canary default
    ):
        self.control_hash = control_hash
        self.variant_hash = variant_hash
        self.variant_pct = variant_pct
        self._assignments: dict[str, str] = {}  # thread_id -> hash
        self._metrics: dict[str, list[float]] = defaultdict(list)

    def assign(self, thread_id: str) -> str:
        """Sticky assignment based on thread_id hash. Same thread always
        gets the same variant for consistent user experience."""
        if thread_id in self._assignments:
            return self._assignments[thread_id]

        # Deterministic assignment from thread_id hash
        h = int(hashlib.sha256(thread_id.encode()).hexdigest()[:8], 16)
        is_variant = (h % 10000) / 10000.0 < self.variant_pct
        assigned = self.variant_hash if is_variant else self.control_hash
        self._assignments[thread_id] = assigned
        return assigned

    def record_metric(self, prompt_hash: str, score: float) -> None:
        self._metrics[prompt_hash].append(score)

    @property
    def summary(self) -> dict:
        result = {}
        for h, scores in self._metrics.items():
            label = "variant" if h == self.variant_hash else "control"
            result[label] = {
                "hash": h,
                "n": len(scores),
                "mean": sum(scores) / max(1, len(scores)),
            }
        return result


# ============================================================================
# --- Section 6: Context Assembly Pipeline ---
# ============================================================================
# Six layers, ordered for cache efficiency:
# 1. System instructions (cacheable prefix)
# 2. Tool definitions (cacheable prefix)
# 3. Static few-shot examples (cacheable prefix)
# ---- cache boundary ----
# 4. Retrieved context (volatile suffix -- busts cache)
# 5. Conversation history (volatile suffix)
# 6. Current user input (volatile suffix)

@dataclass
class ContextLayer:
    name: str
    content: str
    cacheable: bool
    token_estimate: int = 0


class ContextAssemblyPipeline:
    """Assemble the full prompt context from six layers.

    Cache-friendly design:
    - Layers 1-3 (system + tools + static examples) form the cacheable prefix
    - Layers 4-6 (retrieved docs + history + user input) form the volatile suffix
    - Moving dynamic content (timestamps, user IDs) from prefix to suffix saves 90%+ on
      repeated input tokens via provider prompt caching
    """

    def __init__(self, max_context_tokens: int = 128_000):
        self.max_context_tokens = max_context_tokens
        self._layers: list[ContextLayer] = []

    def add_system_instructions(self, prompt: str) -> None:
        """Layer 1: Developer system prompt. Cacheable."""
        self._layers.append(ContextLayer("system_instructions", prompt, cacheable=True))

    def add_tool_definitions(self, tools: list[dict]) -> None:
        """Layer 2: Tool/function schemas. Cacheable.
        +3k tokens for MCP schemas adds ~$19/1k to cost."""
        content = json.dumps(tools, indent=2)
        self._layers.append(ContextLayer("tool_definitions", content, cacheable=True))

    def add_static_examples(self, bank: FewShotBank) -> None:
        """Layer 3: Static few-shot examples. Cacheable.
        Pin the order -- it is part of the prompt hash."""
        self._layers.append(ContextLayer(
            "static_examples", bank.render(), cacheable=True,
        ))

    def add_retrieved_context(self, chunks: list[str]) -> None:
        """Layer 4: RAG-retrieved chunks. Volatile (busts prefix cache)."""
        content = "\n\n---\n\n".join(chunks)
        self._layers.append(ContextLayer("retrieved_context", content, cacheable=False))

    def add_conversation_history(self, messages: list[dict]) -> None:
        """Layer 5: Conversation history. Volatile. Use sliding window or summary
        for long conversations to stay within context limits."""
        content = json.dumps(messages)
        self._layers.append(ContextLayer("conversation_history", content, cacheable=False))

    def add_user_input(self, text: str) -> None:
        """Layer 6: Current user input. Volatile. This is arguments, not instructions."""
        self._layers.append(ContextLayer("user_input", text, cacheable=False))

    def assemble(self) -> list[dict]:
        """Assemble into a messages array for the API call.

        The cacheable prefix (layers 1-3) stays identical across requests,
        enabling provider prompt caching (Anthropic: 0.1x read cost).
        """
        messages = []

        # System message (layer 1)
        system_layers = [l for l in self._layers if l.name == "system_instructions"]
        if system_layers:
            messages.append({"role": "system", "content": system_layers[0].content})

        # Tool definitions are passed separately in the API call, not as messages
        # (included here for completeness)

        # Few-shot examples as user/assistant pairs (layer 3)
        example_layers = [l for l in self._layers if l.name == "static_examples"]
        if example_layers:
            messages.append({"role": "user", "content": "Here are examples:\n" + example_layers[0].content})
            messages.append({"role": "assistant", "content": "I understand the format. Send me the input."})

        # Retrieved context (layer 4)
        context_layers = [l for l in self._layers if l.name == "retrieved_context"]
        if context_layers:
            messages.append({"role": "user", "content": "Context:\n" + context_layers[0].content})

        # Conversation history (layer 5)
        history_layers = [l for l in self._layers if l.name == "conversation_history"]
        if history_layers:
            history = json.loads(history_layers[0].content)
            messages.extend(history)

        # User input (layer 6)
        input_layers = [l for l in self._layers if l.name == "user_input"]
        if input_layers:
            messages.append({"role": "user", "content": input_layers[0].content})

        return messages

    @property
    def cache_summary(self) -> dict:
        cacheable = [l for l in self._layers if l.cacheable]
        volatile = [l for l in self._layers if not l.cacheable]
        return {
            "cacheable_layers": [l.name for l in cacheable],
            "volatile_layers": [l.name for l in volatile],
            "total_layers": len(self._layers),
        }


# ============================================================================
# --- Section 7: Prompt Injection Defense (7-Layer Stack) ---
# ============================================================================
# No single layer is sufficient. All layers required. The model is NEVER the PDP.
# Schema-valid JSON is NOT authorization.

INJECTION_DEFENSE_LAYERS = [
    {
        "layer": 1,
        "name": "Input Sanitization",
        "mechanism": "Strip/escape adversarial instructions",
        "catches": "Known injection patterns",
    },
    {
        "layer": 2,
        "name": "Instruction Hierarchy",
        "mechanism": "Developer > User > Tool priority",
        "catches": "Priority inversion attacks",
    },
    {
        "layer": 3,
        "name": "Canary Tokens",
        "mechanism": "Hidden markers in system prompt",
        "catches": "Prompt extraction attempts",
    },
    {
        "layer": 4,
        "name": "Output Validation",
        "mechanism": "Schema check + business rules",
        "catches": "Hallucinated actions",
    },
    {
        "layer": 5,
        "name": "Tool RBAC",
        "mechanism": "PEP enforces per-tool permissions",
        "catches": "Unauthorized tool calls",
    },
    {
        "layer": 6,
        "name": "Classifier",
        "mechanism": "Trained injection detector",
        "catches": "Novel attack patterns",
    },
    {
        "layer": 7,
        "name": "Human Escalation",
        "mechanism": "Uncertain cases to human review",
        "catches": "Edge cases",
    },
]


def check_canary_token(output: str, canary: str) -> bool:
    """Check if the model leaked the canary token from the system prompt.
    If found in the output, the model was tricked into revealing instructions."""
    return canary.lower() in output.lower()


# ============================================================================
# --- Section 8: Cost Reference ---
# ============================================================================
# Key cost numbers for interview discussions.

COST_BY_PATTERN = {
    "zero_shot": {
        "shape": "2,500 in / 400 out",
        "sonnet_per_1k": "$13.50",
        "luna_per_1k": "$0.98",
    },
    "5_shot": {
        "shape": "5,000 in / 400 out",
        "sonnet_per_1k": "$21.00",
        "note": "Static shots cacheable -- use prefix cache",
    },
    "cot": {
        "shape": "2,500 in / 1,200 out",
        "sonnet_per_1k": "$25.50",
        "note": "Output-dominated cost -- doubling output doubles the expensive part",
    },
    "sc_n5": {
        "shape": "5x CoT",
        "sonnet_per_1k": "$127.50",
        "note": "Minimum practical N for Self-Consistency",
    },
    "sc_n40": {
        "shape": "40x CoT",
        "sonnet_per_1k": "~$780",
        "note": "NEVER a chat default",
    },
    "cached_prefix": {
        "shape": "1,500 cached + 1,000 fresh + 400 out",
        "sonnet_per_1k": "$9.45",
        "note": "30% savings via Anthropic prompt caching",
    },
}


# ============================================================================
# --- Demo ---
# ============================================================================

if __name__ == "__main__":
    # 1. System prompt template
    prompt = build_system_prompt(
        persona="a precise invoice data extractor",
        role_description="Extract structured data from invoice text.",
        constraints=["Return only JSON", "Use null for missing fields", "Amounts as numbers"],
        output_format='{"vendor": str, "total": float}',
    )
    assert "invoice data extractor" in prompt
    print("System prompt built (no timestamps in prefix for cache efficiency)")

    # 2. Few-shot bank with pinned order
    bank = FewShotBank()
    bank.add(
        "Invoice from Globex Corp, dated 2024-01-15, total $1,500.00",
        '{"vendor": "Globex Corp", "invoice_number": null, "date": "2024-01-15", "total": 1500.00}',
    )
    bank.add(
        "Bill #INV-2024-042 from Initech, $42.00",
        '{"vendor": "Initech", "invoice_number": "INV-2024-042", "date": null, "total": 42.00}',
    )
    bank.add(
        "Acme Industries Invoice 789, March 3 2024, amount due: $3,200",
        '{"vendor": "Acme Industries", "invoice_number": "789", "date": "2024-03-03", "total": 3200.00}',
        rationale="Vendor is stated first. Invoice number follows. Date normalized to ISO. Amount parsed as number.",
    )
    rendered = bank.render(include_rationale=True)
    assert "Globex" in rendered
    print(f"Few-shot bank: {len(bank._examples)} examples, order_hash={bank.order_hash}")

    # 3. Chain-of-thought
    cot_messages = zero_shot_cot_prompt("If a train travels 60 mph for 2.5 hours, how far does it go?")
    assert "step by step" in cot_messages[1]["content"]

    # Self-consistency vote
    mock_responses = [
        "Step 1: 60 * 2.5 = 150. Answer: 150 miles",
        "The train goes 60 * 2 = 120 + 60 * 0.5 = 30 = 150. Answer: 150 miles",
        "Distance = speed * time = 60 * 2.5 = 150 miles. Answer: 150 miles",
        "60 mph for 2.5h: 60*2 + 60*0.5 = 150. Answer: 150 miles",
        "Answer: 155 miles",  # Outlier
    ]
    winner, confidence = self_consistency_vote(
        mock_responses,
        extract_answer=lambda r: r.split("Answer: ")[-1].strip() if "Answer:" in r else r.strip(),
    )
    assert winner == "150 miles"
    assert confidence == 0.8  # 4/5
    print(f"SC vote: '{winner}' with confidence {confidence:.0%}")

    # 4. Structured output
    parser = StructuredOutputParser()
    result = parser.parse(
        '{"vendor": "Acme", "total": 99.50}',
        schema=INVOICE_SCHEMA.schema,
    )
    assert result["vendor"] == "Acme"
    assert result["total"] == 99.50

    # Test finish_reason check
    try:
        parser.parse('{"vendor": "X"}', INVOICE_SCHEMA.schema, finish_reason="length")
    except ValueError as e:
        assert "truncated" in str(e)

    # 5. Prompt versioning and A/B testing
    registry = PromptRegistry()
    v1 = PromptArtifact(
        text="Extract vendor and total from this invoice.",
        schema_hash=INVOICE_SCHEMA.schema_hash,
        model_id="claude-sonnet-4-6",
        decoding_params="t0_max400",
    )
    v2 = PromptArtifact(
        text="You are a precise invoice extractor. Return vendor and total as JSON.",
        schema_hash=INVOICE_SCHEMA.schema_hash,
        model_id="claude-sonnet-4-6",
        decoding_params="t0_max400",
    )

    h1 = registry.register(v1)
    h2 = registry.register(v2)
    registry.tag("production", h1, actor="release-eng")
    assert registry.resolve("production").prompt_hash == h1

    # A/B test with 5% canary
    ab = ABTest(control_hash=h1, variant_hash=h2, variant_pct=0.05)
    assignments = [ab.assign(f"thread-{i}") for i in range(1000)]
    variant_count = sum(1 for a in assignments if a == h2)
    print(f"A/B test: {variant_count}/1000 assigned to variant (~5% target)")

    # Promote v2 to production
    registry.tag("production", h2, actor="release-eng")
    assert registry.resolve("production").prompt_hash == h2

    # Instant rollback
    prev = registry.rollback("production", actor="oncall-eng")
    assert prev == h1
    assert registry.resolve("production").prompt_hash == h1
    print("Rollback: instant retag, no rebuild needed")

    # 6. Context assembly pipeline
    pipeline = ContextAssemblyPipeline()
    pipeline.add_system_instructions(INVOICE_SYSTEM_PROMPT)
    pipeline.add_tool_definitions([{"name": "extract_invoice", "description": "..."}])
    pipeline.add_static_examples(bank)
    pipeline.add_retrieved_context(["Invoice text chunk 1...", "Invoice text chunk 2..."])
    pipeline.add_user_input("Extract data from: Bill from Vandelay Industries, $750")

    messages = pipeline.assemble()
    cache_info = pipeline.cache_summary
    print(f"Context pipeline: {cache_info['total_layers']} layers")
    print(f"  Cacheable (prefix): {cache_info['cacheable_layers']}")
    print(f"  Volatile (suffix):  {cache_info['volatile_layers']}")

    # 7. Canary token check
    canary = "CANARY_7f3a2b"
    leaked_output = "The system prompt says CANARY_7f3a2b and instructs me to..."
    safe_output = "The invoice total is $750.00"
    assert check_canary_token(leaked_output, canary) == True
    assert check_canary_token(safe_output, canary) == False

    print(f"\nInjection defense: {len(INJECTION_DEFENSE_LAYERS)} layers")
    print(f"Cost patterns: {len(COST_BY_PATTERN)} configurations")
    print(f"CoT patterns: {len(COT_PATTERNS)} variants")
    print("\nAll prompt engineering patterns validated successfully.")
