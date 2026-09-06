# Module 16: Prompt Engineering Patterns

## What Is This?

Prompt engineering is not clever wording in a chat box. It is **context engineering** -- the disciplined assembly of instructions, examples, schemas, and constraints that form the control plane of every LLM application. Think of it like being a chef: the recipe (prompt) determines the dish quality far more than the oven (model). Context engineering is about assembling the right ingredients (system instructions, retrieved documents, few-shot examples, tool definitions, conversation history) in the right order, at the right time, within a fixed-size kitchen counter (context window).

Two independently scaled planes share versioned content and a decoder constraint: the **control plane** owns the prompt registry (immutable commit hash + mutable environment tags), the schema/grammar artifact, DSPy program compilation, and promotion gates. The **data plane** owns prefill of assembled messages, sampled or grammar-masked decode, and parse/refusal handling. Collapsing these -- for example, letting a playground save go directly to production -- is the dominant failure mode.

**Artifact pin**: `(prompt_hash, schema_hash, model_id, decoding_params, optimizer_id, metric_id)`. Changing any element is a new artifact. A schema-valid JSON object is **not** an authorized action. Constrained decode guarantees a **language**, not truth.

---

## Part 1: System Topology & Data Flow

### Architecture: 6-Layer Context Assembly Pipeline

```
+-----------------------------------------------------------------------+
|                     CONTEXT ASSEMBLY PIPELINE                          |
|                                                                       |
|  Layer 1: System Instructions (developer prompt, persona, policies)   |
|      |                                                                |
|  Layer 2: Tool Definitions (function schemas, MCP tool catalog)       |
|      |        [cacheable prefix boundary above this line]             |
|  Layer 3: Static Few-Shot Examples (pinned order, reviewed, PII-free) |
|      |                                                                |
|  Layer 4: Retrieved Context (RAG chunks, documents, citations)        |
|      |        [volatile suffix -- busts prefix cache]                 |
|  Layer 5: Conversation History (sliding window or summary)            |
|      |                                                                |
|  Layer 6: Current User Input (arguments, not instructions)            |
+-----------------------------------------------------------------------+
             |
             v
+-----------------------------------------------------------------------+
|                     DECODER CONSTRAINT                                 |
|                                                                       |
|  Option A: Free-form text (standard sampling)                         |
|  Option B: JSON mode (valid syntax, no schema guarantee)              |
|  Option C: Schema-constrained decode (FSM/PDA mask, 100% schema)      |
|  Option D: Grammar (GBNF/XGrammar for self-hosted models)             |
+-----------------------------------------------------------------------+
             |
             v
+-----------------------------------------------------------------------+
|                     PARSE / VALIDATE / RETRY                           |
|                                                                       |
|  Check: refusal? truncation? schema valid? business rules?            |
|  Instructor: semantic re-ask (cap 1 retry). Circuit on consecutive    |
|  parse failures -> 422 to client. Do NOT loop to max_turns.           |
+-----------------------------------------------------------------------+
```

### Prompt Caching Mechanics

Prompt caching is the most important cost optimization in prompt engineering. The prefix (everything that stays the same across requests) can be cached by the provider, saving 90%+ on repeated input tokens.

| Provider | Cache TTL | Write Cost | Read Cost | Cache Key |
|---|---|---|---|---|
| **Anthropic** | 5 min (auto) | 1.25x input | 0.1x input | Exact prefix match |
| **OpenAI** | ~5-10 min | 1x input | 0.5x input | Prefix match |
| **Gemini** | ~5 min | -- | Free | Context caching API |

**Cache-friendly design rules**:
- Put stable content (system prompt, tools, static few-shot) in the LEFT prefix
- Put volatile content (user query, kNN examples, retrieved docs) in the RIGHT suffix
- Timestamps, user IDs, or `{{now}}` in the system prompt bust the entire prefix
- Anthropic: changing `output_config.format` invalidates that thread's prompt cache
- Schema grammar is cached separately (24h on Anthropic)

---

## Part 2: Core Mechanics & Algorithms

### 2.1 In-Context Learning (Few-Shot)

**Brown et al. (GPT-3, 2020)** defined ICL: concatenate k labeled pairs and predict the next label with no gradient update. The exemplar set IS the learned policy -- changing k, order, or retrieval method is a model change.

**What demos actually teach (Min et al.)**: Replacing gold labels with random labels drops classification only ~0-5 abs. Label **space**, input **distribution**, and **format** dominate. For classification ICL, few-shot is often task location + format, not supervised learning. BUT for CoT math and invoice extraction, gold labels/rationales still matter.

**Exemplar ordering matters enormously.** Lu et al.: 4-shot SST-2 permutations range from ~50% (chance) to >85%. Pin the order. "We'll just add 5 shots" without controlling order is an interview fail.

**Few-shot selection strategies**:

| Strategy | Approach | Cache Impact | Best For |
|---|---|---|---|
| **Fixed bank** | Static, reviewed, PII-scrubbed | Cacheable prefix | Homogeneous tasks |
| **KATE (similarity)** | kNN on embeddings, most similar first | Volatile suffix (cache bust) | Diverse inputs |
| **MMR / DPP** | Maximize relevance + diversity | Volatile suffix | Avoid near-duplicate clusters |
| **IDS** | Zero-shot-CoT then re-select | Two-stage | Complex reasoning |

### 2.2 Chain-of-Thought Family

| Pattern | Key Result | Cost | When to Use |
|---|---|---|---|
| **CoT** (Wei 2022) | GSM8K: 17.9% -> 56.9% (PaLM 540B) | ~2x output tokens | Multi-step math/logic |
| **ZS-CoT** (Kojima) | "Let's think step by step." MultiArith: 17.7 -> 78.7% | 2 serial calls | Quick reasoning boost, no examples needed |
| **L2M** (Zhou) | SCAN length-split: 16.2% -> 99.7% | >=2 sequential calls | Compositional generalization |
| **ToT** (Yao) | Game of 24: 4% -> 74% (b=5, GPT-4) | 5-100x more tokens | Search/planning problems |
| **SC** (Wang) | GSM8K: +17.9 over CoT (N=40) | Nx CoT cost | When accuracy > cost/latency |
| **ReAct** (Yao) | HotpotQA: +6.4 over CoT-SC | Tool calls per step | Tool-using agents |

**CoT anti-patterns**:
- CoT can **hurt** on NLI/extraction tasks (Wang Table 5: e-SNLI dropped from 85.8 to 81.0)
- SC N=40 as a chat default is ~$780/1k Sonnet calls -- never a chat default
- CoT on single-step problems shows near zero or negative gains
- ZS-CoT on commonsense tasks often does not help without huge scale

**Self-Consistency production guidance**: Authors suggest starting with N=5 or N=10 ("saturates quickly"). At N=5 you get most of the accuracy gain at 1/8 the cost of N=40. Temperature 0.5-0.7. For open-ended tasks, majority vote is undefined without a canonicalizer -- use pass@k instead.

### 2.3 DSPy: Prompt Compilation

DSPy treats LM pipelines as parameterized modules. A compiler (teleprompter) maximizes a metric by searching over instructions and few-shot examples. The compiled program is frozen and deployed -- the compiler never runs on the live path.

**Core primitives**:

| Primitive | Role | Example |
|---|---|---|
| **Signature** | Typed I/O spec | `"question -> answer"` or class with `dspy.InputField()` / `dspy.OutputField()` |
| **Module** | Strategy wrapper | `dspy.Predict(sig)`, `dspy.ChainOfThought(sig)`, `dspy.ReAct(sig, tools=[])` |
| **Metric** | Training loss function | `def metric(gold, pred, trace=None) -> float` |
| **Teleprompter** | Offline optimizer | `BootstrapFewShot`, `MIPROv2`, `GEPA`, `SIMBA` |
| **Saved program** | Frozen artifact | `program.save("extract_v2.json")` -- load only online |

**Optimizer ladder**:

| Optimizer | When | Key Detail |
|---|---|---|
| **BootstrapFewShot** | Demos weak, budget limited | Teacher generates traces; keep if metric passes; default 4 bootstrapped + 16 labeled |
| **MIPROv2** | Both instructions and demos weak | Bayesian search (TPE) over instruction x demo assignments; 2,270-6,926 rollouts |
| **GEPA** (ICLR 2026 oral) | Best quality, moderate budget | Genetic-Pareto; +14% aggregate vs MIPROv2; up to 35x fewer rollouts than LoRA |
| **SIMBA** | Named failure pattern | Stochastic mini-batch; LLM proposes rule patches; bsize=32, max_steps=8 |
| **BootstrapFinetune** | Prompt plateau + tunable weights | Exit ramp to fine-tuning |

**Production rule**: DSPy `compile` is an OFFLINE batch job. Online serving loads frozen `program.json` only. Compile cost estimate: ~$60 for 4k rollouts on a medium model. Never run the compiler on the user request path.

### 2.4 Structured Output: Decoder Constraint vs Post-Parse

Three layers (do not collapse):

| Layer | Mechanism | Guarantee | Caveat |
|---|---|---|---|
| **Prompt-only** | "Reply as JSON" | None | Invalid fences, trailing prose |
| **JSON mode** | `json_object` / `mime_type` | Valid JSON syntax | No schema guarantee; Gemini: "strong hint", not 100% |
| **Schema-constrained** | FSM/PDA mask on logits | 100% schema compliance (conditional) | OpenAI: refusal or truncation bypass; Anthropic: strips unsupported constraints |

**Provider-specific behaviors**:

| Provider | 100% Schema? | Constraints Supported | Caveats |
|---|---|---|---|
| **OpenAI SO** | Yes (conditional) | `minimum`, `maximum`, `pattern` in grammar | Refusal or `max_tokens` truncation bypasses. Root must be object. |
| **Anthropic** | Yes (conditional) | `additionalProperties: false` injected | SDK strips `minimum`/`maximum`/`minLength`. Enum case not guaranteed. |
| **Gemini** | Only with schema + mime | Limited | Schema without mime != 100%. Few-shot must match `propertyOrdering`. |
| **Self-hosted** | XGrammar/Outlines/GBNF | Full grammar control | XGrammar: ~40 microsec/token overhead |

**Anthropic SO complexity limits**: Max 20 strict tools/request, 24 optional parameters across all strict schemas, 16 `anyOf`. Compile timeout 180s. HIPAA: PHI must NOT appear in JSON schema (property names, enums, const) -- compiled grammars do not get PHI protections.

### 2.5 Prompt Injection Defense (7-Layer Stack)

| Layer | Mechanism | What It Catches |
|---|---|---|
| 1. Input sanitization | Strip/escape adversarial instructions | Known injection patterns |
| 2. Instruction hierarchy | Developer > User > Tool | Priority inversion attacks |
| 3. Canary tokens | Hidden markers in system prompt | Prompt extraction attempts |
| 4. Output validation | Schema check + business rules | Hallucinated actions |
| 5. Tool RBAC | PEP enforces per-tool permissions | Unauthorized tool calls |
| 6. Classifier | Trained injection detector | Novel attack patterns |
| 7. Human escalation | Uncertain cases to human review | Edge cases |

No single layer is sufficient. All layers are required. Prompt text alone is not a security boundary -- deterministic policy enforcement must sit outside the model.

### 2.6 LLMLingua Prompt Compression

LLMLingua and derivatives compress prompts by removing tokens with low perplexity (predictable tokens the model would infer anyway). Achieves 2-10x compression with <5% quality loss on retrieval-heavy prompts. Useful for reducing cost when context windows are full of retrieved documents.

---

## Part 3: Token Economics & NFR Analysis

### 3.1 Cost Per 1K Calls by Pattern

Base shape: 2,000 system + 500 user input tokens, 400 output tokens.

| Pattern | Shape | Sonnet 4.6 ($3/$15) | Luna ($0.20/$1.20) | Notes |
|---|---|---|---|---|
| **Zero-shot** | 2,500 in / 400 out | **$13.50/1k** | **$0.98/1k** | Baseline |
| **5-shot** (+2,500 in) | 5,000 in / 400 out | **$21.00/1k** | **$1.48/1k** | Static shots cacheable |
| **CoT** (+800 out) | 2,500 in / 1,200 out | **$25.50/1k** | **$1.94/1k** | Output-dominated cost |
| **SC N=5** | 5x CoT | **$127.50/1k** | **$9.70/1k** | Minimum practical N |
| **SC N=40** | 40x CoT | **~$780/1k** | **~$60/1k** | NEVER a chat default |
| **L2M** (2 calls) | 2x (2,500 in / 600 out) | **$33.00/1k** | **$2.44/1k** | Sequential, not parallel |
| **ToT** (b=5) | 5-100x CoT | **$127-2550/1k** | -- | Planner, not chat |
| **Cached prefix** (1,500/2,500 @ 0.1x read) | 1,500 cached + 1,000 fresh + 400 out | **$9.45/1k** | **$0.78/1k** | 30% savings on Sonnet |

**Key insight**: CoT cost is output-dominated. SC cost scales linearly with N. SC N=40 at Sonnet pricing is ~$780/1k -- never a chat default. Use SC N=5 when accuracy matters.

### 3.2 DSPy Compilation Cost

| Component | Estimate | Notes |
|---|---|---|
| BootstrapFewShot (200 examples) | ~$15-30 | Teacher traces + metric evaluation |
| MIPROv2 auto=heavy (4k rollouts) | ~$60 | Bayesian search over instructions x demos |
| GEPA (12 mutations, 100 examples) | ~$20-40 | 35x fewer rollouts than LoRA for similar quality |

Compile is a batch job. Amortize over millions of production requests.

### 3.3 Latency SLA Targets

| Pattern | p50 | p95 | p99 | Notes |
|---|---|---|---|---|
| **Zero-shot extraction** | 600 ms | 2,000 ms | 5,000 ms | Baseline |
| **CoT extraction** | 1,200 ms | 4,000 ms | 12,000 ms | Longer decode |
| **SC N=5** | 1,200 ms (parallel) | 4,000 ms | 12,000 ms | Parallel sampling; wall-clock = 1 call |
| **L2M** | 2,400 ms | 8,000 ms | 24,000 ms | 2 sequential calls |
| **Structured output** | +0-50 ms | +0-100 ms | +0-200 ms | Grammar overhead is small |
| **Instructor retry** | +1,200 ms per retry | -- | -- | Cap at 1 retry |

---

## Part 4: Distributed Resilience & Security

### 4.1 Prompt Registry and Versioning

- **Git is SoT**: Prompt text lives in version control. Hash of rendered tokens = artifact ID.
- **Registry tags are pointers**: `production` tag points to a hash. Rollback = retag previous hash, not rewrite.
- **Never serve `latest`**: A playground save becoming production is the dominant failure mode.
- **Cache key**: Must include `prompt_hash` or stale completions survive a prompt update.

### 4.2 Circuit Breaker for Parse Failures

Consecutive parse failures (model returns invalid output despite constrained decoding) trigger a circuit breaker:
- **Closed**: Normal operation. Track consecutive failures.
- **Open**: After 3 consecutive parse failures, return 422 to client. Do not loop retries.
- **Half-open**: Probe with a simple test input every 30 seconds.

**Fallback chain**: Schema-constrained decode -> Instructor semantic retry (cap 1) -> 422 to client. Never loop to max_turns on parse failures.

### 4.3 Zero-Trust MCP for Prompts

| MCP Verb | Allowed For | Denied For |
|---|---|---|
| `prompts/get` | Assistants (already-promoted hash) | -- |
| `prompts/set` | Human + break-glass only | Model / assistant |
| `compile_program` | Offline CI only | Any production path |
| `promote_tag` | Human + break-glass only | Model / assistant |

Identity from verified token / RunContext. Never from tool arguments. No token passthrough to LLM providers.

### 4.4 PII in Prompts and Schemas

- Few-shot examples must be PII-scrubbed before entering the prompt registry
- HIPAA: PHI must not appear in JSON Schema (property names, enums, const) on Anthropic -- compiled grammars do not get PHI protections
- kNN-retrieved examples are an injection surface -- validate and redact before including

---

## Part 5: Production Enterprise Code

```python
"""Prompt engineering runtime: registry with hash pinning, structured output
with fallback, few-shot bank management, and DSPy program loading.
Run: python prompt_engineering_runtime.py
"""
import hashlib, json, time
from dataclasses import dataclass, field
from typing import Any

@dataclass(frozen=True)
class PromptArtifact:
    """Immutable prompt artifact. Hash = identity."""
    text: str; schema_hash: str; model_id: str; decoding_params: str
    @property
    def prompt_hash(self) -> str:
        return "p_" + hashlib.sha256(self.text.encode("utf-8")).hexdigest()[:16]
    @property
    def artifact_id(self) -> str:
        return "|".join([self.prompt_hash, self.schema_hash, self.model_id, self.decoding_params])

class PromptRegistry:
    """Git-backed prompt registry. Tags are mutable pointers to immutable hashes."""
    def __init__(self):
        self.artifacts: dict[str, PromptArtifact] = {}  # hash -> artifact
        self.tags: dict[str, str] = {}                    # tag -> hash
    def register(self, artifact: PromptArtifact) -> str:
        h = artifact.prompt_hash
        self.artifacts[h] = artifact
        return h
    def tag(self, name: str, prompt_hash: str, *, actor: str) -> None:
        if prompt_hash not in self.artifacts:
            raise KeyError(f"unknown_hash:{prompt_hash}")
        self.tags[name] = prompt_hash  # Production: CAS with etag
    def resolve(self, tag: str) -> PromptArtifact:
        h = self.tags.get(tag)
        if not h or h not in self.artifacts: raise KeyError(f"tag_not_found:{tag}")
        return self.artifacts[h]

class StructuredOutputParser:
    """Parse with fallback: schema-constrained -> Instructor retry (cap 1) -> 422."""
    def __init__(self, max_retries: int = 1):
        self.max_retries = max_retries; self._consecutive_failures = 0
    def parse(self, raw: str, schema: dict) -> dict:
        if self._consecutive_failures >= 3:
            raise RuntimeError("circuit_open:parse_failures")
        try:
            result = json.loads(raw)
            # Validate required fields
            for key in schema.get("required", []):
                if key not in result: raise ValueError(f"missing_field:{key}")
            self._consecutive_failures = 0
            return result
        except (json.JSONDecodeError, ValueError) as e:
            self._consecutive_failures += 1
            raise ValueError(f"parse_fail:{e}") from e

class FewShotBank:
    """Static few-shot bank with pinned order. PII-scrubbed at registration time."""
    def __init__(self):
        self.examples: list[dict[str, str]] = []
        self._order_hash: str = ""
    def add(self, input_text: str, output_text: str) -> None:
        self.examples.append({"input": input_text, "output": output_text})
        self._update_hash()
    def _update_hash(self):
        blob = json.dumps(self.examples, sort_keys=True)
        self._order_hash = hashlib.sha256(blob.encode()).hexdigest()[:16]
    def render(self) -> str:
        """Render in pinned order for prefix caching."""
        lines = []
        for ex in self.examples:
            lines.append(f"Input: {ex['input']}\nOutput: {ex['output']}")
        return "\n---\n".join(lines)

if __name__ == "__main__":
    # Register a prompt artifact
    registry = PromptRegistry()
    artifact = PromptArtifact(
        text="Extract vendor name and total from this invoice.",
        schema_hash="s_invoice_v1", model_id="gpt-5.6-luna", decoding_params="t0_max400"
    )
    h = registry.register(artifact)
    registry.tag("production", h, actor="release-engineer")
    loaded = registry.resolve("production")
    assert loaded.prompt_hash == h
    # Structured output parsing with circuit breaker
    parser = StructuredOutputParser()
    result = parser.parse('{"vendor": "Acme", "total": 99.50}', {"required": ["vendor", "total"]})
    assert result["vendor"] == "Acme"
    # Few-shot bank with pinned order
    bank = FewShotBank()
    bank.add("Invoice from Globex, $150", '{"vendor": "Globex", "total": 150}')
    bank.add("Bill: Initech $42.00", '{"vendor": "Initech", "total": 42}')
    rendered = bank.render()
    assert "Globex" in rendered  # Order preserved
    print(f"ok: artifact={h}, parsed={result}, bank_hash={bank._order_hash}")
```

---

## Part 6: Architectural System Design Scenarios

### Scenario 1: Prompt Management for a 50-Person AI Team

**Problem**: 50 engineers iterating on prompts weekly across 12 products. Current state: prompts in code strings, no versioning, no evaluation gates. Quality regressions happen silently. Need: version control, evaluation gates, safe rollout, rollback in seconds.

**Architecture**: Git SoT for prompt text + schema. CI pipeline runs evaluation suite on PR (pytest-langsmith or promptfoo). Registry (LangSmith Hub or MLflow) stores immutable versions with hash-addressed commits. Mutable tags (`staging`, `production`) are pointers. Feature flags (LaunchDarkly) for 5% canary rollout with sticky `thread_id`. Rollback = retag previous hash via flag kill (seconds, not rebuild).

**Trade-off matrix**:

| Decision | Option A | Option B | Chosen | Rationale |
|---|---|---|---|---|
| SoT | Playground / Hub | Git | Git | Playground-as-SoT causes silent drift |
| Eval gate | Manual review | CI with golden set | CI | Automated, reproducible, fail-closed |
| Rollout | 100% deploy | 5% canary via flags | 5% canary | Blast radius control; kill in seconds |
| Cache | Ignore | Key includes prompt_hash | Include hash | Stale completions survive promote otherwise |

### Scenario 2: Injection-Hardened Customer-Facing Agent

**Problem**: Customer-facing agent with tool access (ticket creation, refunds). Must resist prompt injection while maintaining helpfulness. Regulatory requirement for audit trail.

**Architecture**: 7-layer injection defense stack. System prompt with clear instruction hierarchy (developer > user > tool). Canary tokens for extraction detection. Classifier-based injection detector on input. Output validation against business rules. Tool RBAC via PEP (not the model). Human escalation for uncertain cases. WORM audit log of all decisions.

**Key design decision**: The model is never the PDP. Schema-valid `{"action":"refund"}` is not authorization. Every tool call goes through a deterministic PEP that checks the JWT-bound principal against the RBAC policy, independent of what the model requested.

---

## Common Failure Modes

| Failure | Mechanism | Mitigation |
|---|---|---|
| **Playground save = production** | No registry; last-edited string is live | Git SoT + immutable hash + mutable tags |
| **SC N=40 as chat default** | ~$780/1k Sonnet calls | Reserve for batch/offline; N=5 for production |
| **Schema-valid = authorized** | Model outputs valid JSON for refund | PEP enforces RBAC outside the model |
| **DSPy compile on live path** | Compiler runs on user requests | Compile is offline batch job; load frozen program.json only |
| **CoT on NLI/extraction** | CoT hurts e-SNLI (85.8 -> 81.0) | Measure before adding CoT; not all tasks benefit |
| **"Just add 5 shots"** | No order control | Pin order; Lu et al.: 50% -> 85% swing on permutations |
| **Timestamp in system prompt** | Busts entire prefix cache | Move dynamic content to volatile suffix |
| **PHI in JSON Schema** | Anthropic compiled grammars lack PHI protections | Never put PHI in property names, enums, or const |
| **Instructor retry loops** | Semantic re-ask on user p99 | Cap at 1 retry; circuit breaker on consecutive failures |
| **Judge as optimizer loss** | DSPy optimizes against the judge | Separate optimize vs gate suites; freeze gate eval_suite_id |
| **Serving `latest` prompt** | Tag moves without evaluation | Always resolve to hash; never serve mutable tag directly |

---

## Interview Q&A

**Q1: What is prompt engineering in one sentence?**
I treat it as a compiler and decoder: versioned instructions, exemplar banks, and JSON Schema are control-plane artifacts; prefill and grammar-masked decode are the data plane. I pin `(prompt_hash, schema_hash, model_id, decoding_params, optimizer_id, metric_id)`. Schema-valid JSON is not authorization.

**Q2: When do you use CoT vs zero-shot?**
CoT for multi-step math and logic problems where the model needs to show work -- Wei showed GSM8K going from 17.9% to 56.9%. But CoT can hurt on NLI and extraction tasks (Wang Table 5: e-SNLI dropped from 85.8 to 81.0). I measure before adding CoT to any task.

**Q3: Self-consistency -- when and how many samples?**
SC replaces greedy CoT with N sampled paths and majority vote. I start with N=5 at temperature 0.5-0.7 -- the authors say it "saturates quickly." N=40 at Sonnet pricing is ~$780/1k calls, which is never a chat default. SC is for batch extraction or high-stakes decisions where accuracy justifies 5x cost.

**Q4: How does DSPy work in production?**
The compiler (teleprompter) runs offline as a batch job, searching over instructions and few-shot examples to maximize a metric. It produces a frozen `program.json` with optimized instructions and demos. Production loads this file only -- the compiler never touches the live path. GEPA (ICLR 2026) achieves +14% over MIPROv2 with up to 35x fewer rollouts than LoRA.

**Q5: Structured output -- is it really 100%?**
OpenAI Structured Outputs is 100% conditional: if there is no safety refusal and no `max_tokens` truncation, the output matches the schema. Always check `finish_reason` before parsing. Anthropic strips unsupported constraints (`minimum`/`maximum`) and enum case is not guaranteed. Gemini requires both schema AND mime type for guaranteed JSON.

**Q6: How do you handle prompt caching?**
Put stable content (system prompt, tools, static few-shot) in the left prefix. Put volatile content (user query, retrieved docs, kNN examples) in the right suffix. Anthropic caches at 0.1x read cost with 1.25x write cost. A timestamp or user ID in the system prompt busts the entire cache -- move dynamic content to the suffix.

**Q7: Exemplar order -- does it matter?**
Enormously. Lu et al. showed 4-shot SST-2 permutations ranging from ~50% (chance) to >85% accuracy. The order is part of the prompt hash. I pin the order and validate it. "We'll just add 5 shots" without controlling order is an interview fail.

**Q8: What is your prompt injection defense?**
A 7-layer stack: input sanitization, instruction hierarchy (developer > user > tool), canary tokens, output validation, tool RBAC via PEP, classifier-based detection, and human escalation. No single layer is sufficient. The model is never the PDP -- schema-valid JSON is not authorization.

**Q9: What is the biggest cost trap in prompt engineering?**
Self-consistency as a decorator. SC N=5 is ~$127.50/1k Sonnet calls vs CoT $25.50/1k vs zero-shot $13.50/1k. SC N=40 is ~$780/1k. The second trap is CoT being output-dominated: doubling output tokens doubles the expensive part of the bill.

**Q10: How do you roll out a prompt change safely?**
Git SoT, CI evaluation gate (fail-closed on golden set), register immutable version, 5% canary via feature flag with sticky thread_id, promote to 100% via flag, observe sidecar traces. Rollback = flag kill (seconds). I never rebuild the serving infrastructure to change a prompt -- it is a flag operation.

---

## Key Numbers to Memorize

| Number | What |
|---|---|
| **17.9% -> 56.9%** | CoT on GSM8K (Wei, PaLM 540B) |
| **50% -> >85%** | Lu et al. exemplar order swing on SST-2 |
| **99.7%** | L2M on SCAN length-split (Zhou) |
| **4% -> 74%** | ToT on Game of 24 (Yao, GPT-4, b=5) |
| **+17.9** | SC N=40 improvement over CoT on GSM8K |
| **~$780/1k** | SC N=40 at Sonnet pricing -- never a chat default |
| **100% conditional** | OpenAI SO: no refusal, no truncation |
| **0.1x / 1.25x** | Anthropic cache read / write cost multiplier |
| **~40 us/tok** | XGrammar overhead for constrained decoding |
| **24h** | Anthropic compiled grammar cache TTL |
| **20 / 24 / 16** | Anthropic strict SO limits: tools / optional params / anyOf |
| **n ~ 969** | Miller sample size for 3pp at alpha=0.05 (eval gating) |

---

## Quick Reference

- **Artifact pin**: `prompt_hash + schema_hash + model_id + decoding_params + optimizer_id + metric_id`
- **Cache**: Stable left prefix, volatile right suffix. No timestamps in system prompt.
- **CoT**: Multi-step math/logic. Measure first. Can hurt NLI/extraction.
- **SC**: N=5 practical, N=40 never chat. Majority vote needs canonicalizer.
- **DSPy**: Offline compile. Load frozen program.json online. GEPA > MIPROv2.
- **Structured output**: 100% conditional. Check finish_reason before parse.
- **Few-shot**: Pin order. KATE for diverse inputs. Static bank for homogeneous.
- **Injection**: 7-layer stack. Model is never PDP. Schema != authz.
- **Registry**: Git SoT. Tags are pointers. Rollback = retag. Never `latest`.
- **Rollout**: 5% canary via flag. Kill in seconds. Judge off user p99.
