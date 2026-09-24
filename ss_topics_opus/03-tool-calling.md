# Module 03: Tool Calling — LLM Function Invocation at Enterprise Scale

> **Audience**: Principal DS / Director-level AI candidates
> **Prep focus**: System design, production failure modes, cost/latency trade-offs, security posture
> **Sources**: 38 primary sources (Anthropic docs, OpenAI docs, MCP spec, BFCL benchmark, academic papers, production postmortems)

---

## 1. System Topology & Data Flow

### 1.1 Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              CONTROL PLANE                                      │
│                                                                                 │
│  ┌──────────────────┐  ┌──────────────────┐  ┌─────────────────┐               │
│  │  Tool Registry    │  │ Schema Validator  │  │  RBAC Enforcer  │               │
│  │  ─────────────    │  │  ──────────────   │  │  ─────────────  │               │
│  │  • JSON Schema    │  │  • strict: true   │  │  • Per-tool ACL │               │
│  │  • Version mgmt   │  │  • Type coercion  │  │  • Tenant scope │               │
│  │  • defer_loading  │  │  • Enum enforce   │  │  • allowed_     │               │
│  │  • Category/tags  │  │  • Pydantic sync  │  │    callers      │               │
│  └────────┬─────────┘  └────────┬─────────┘  └───────┬─────────┘               │
│           │                     │                     │                          │
│  ┌────────┴─────────┐  ┌───────┴──────────┐  ┌──────┴──────────┐               │
│  │  Rate Limiter     │  │ Budget Guardrail  │  │ Token Optimizer │               │
│  │  ─────────────    │  │  ──────────────   │  │  ─────────────  │               │
│  │  • Per-tool RPM   │  │  • Max tokens/    │  │  • Tool gating  │               │
│  │  • Per-tenant     │  │    conversation   │  │  • Prompt cache  │               │
│  │  • Read vs write  │  │  • Cost ceiling   │  │  • Credential   │               │
│  │    tiers          │  │  • Chargeback     │  │    stripping    │               │
│  └──────────────────┘  └──────────────────┘  └─────────────────┘               │
└───────────────────────────────────┬─────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                               DATA PLANE                                        │
│                                                                                 │
│  ┌──────────────────────────────────────────────────────────────────────────┐   │
│  │                        Tool Dispatch Engine                              │   │
│  │                                                                          │   │
│  │  ①  Model Decision ──► ②  Parameter Extraction ──► ③  Schema Validation │   │
│  │          │                      │                          │              │   │
│  │          ▼                      ▼                          ▼              │   │
│  │  stop_reason:           JSON parse from              Pydantic validate   │   │
│  │  "tool_use"             content block                + strict mode       │   │
│  │                                                            │              │   │
│  │  ④  Execution Sandbox ◄────────────────────────────────────┘              │   │
│  │          │                                                                │   │
│  │          ├── Docker container (untrusted tools)                           │   │
│  │          ├── WASM runtime (lightweight isolation)                         │   │
│  │          └── Subprocess + cgroups (resource limits)                       │   │
│  │          │                                                                │   │
│  │  ⑤  Result Validator ──► ⑥  Context Injector                             │   │
│  │          │                      │                                         │   │
│  │          ├── Injection scan     ├── Truncation (first N chars)            │   │
│  │          ├── Type check         ├── Projection (relevant fields)          │   │
│  │          └── Size check         └── tool_result block assembly            │   │
│  └──────────────────────────────────────────────────────────────────────────┘   │
└───────────────────────────────────┬─────────────────────────────────────────────┘
                                    │
         ┌──────────────────────────┼──────────────────────────┐
         ▼                          ▼                          ▼
┌─────────────────┐   ┌──────────────────────┐   ┌──────────────────────┐
│  TOOL PROXIES   │   │  PERSISTENCE LAYER   │   │  TELEMETRY SINKS     │
│                 │   │                      │   │                      │
│  • MCP servers  │   │  • Tool definitions  │   │  • OpenTelemetry     │
│    (stateless   │   │    (versioned)       │   │    traces            │
│    2026-07-28)  │   │  • Execution logs    │   │  • Per-tool P50/     │
│  • API connec-  │   │    (audit trail)     │   │    P95/P99 latency   │
│    tors (REST,  │   │  • State checkpoints │   │  • Error rate by     │
│    GraphQL)     │   │    (saga/resume)     │   │    type              │
│  • Server-side  │   │  • Prompt cache      │   │  • LangSmith /       │
│    tools (web_  │   │    (stable prefix)   │   │    Langfuse spans    │
│    search,      │   │                      │   │  • Circuit breaker   │
│    code_exec)   │   │                      │   │    state changes     │
└─────────────────┘   └──────────────────────┘   └──────────────────────┘
```

### 1.2 End-to-End Request Flow

**Step 1 — Client sends request.** The application posts to the LLM API (`POST /v1/messages` for Anthropic, Responses API for OpenAI) with a `tools` array and the conversation messages. Tool definitions are serialized into the model's input context and counted as input tokens. There is no "free metadata" concept.

**Step 2 — Control plane intercepts.** Before the request reaches the model, the control plane validates the tool list against the tenant's RBAC policy, strips deferred tools (`defer_loading: true`) from context to preserve prompt cache, and enforces the token budget. Credentials are removed from schemas (resolved at execution time via vault injection).

**Step 3 — Model reasons and emits tool calls.** The model returns `stop_reason: "tool_use"` (Anthropic) or `function_call` items (OpenAI) with structured JSON parameters. If multiple tools are needed and independent, the model can emit them in a single response for parallel execution.

**Step 4 — Data plane dispatches.** The dispatch engine parses the tool call blocks, validates parameters against the registered schema (Pydantic `model_validate_json()` or JSON Schema strict mode), and routes to the appropriate execution sandbox. If validation fails, the structured error is returned to the model as a `tool_result` for self-correction (capped at 3 retries).

**Step 5 — Tool executes in sandbox.** The tool runs in an isolated environment (Docker, WASM, or subprocess with resource limits). The circuit breaker monitors per-tool failure rates; if a tool fails 3 times in 5 minutes, the circuit opens and all calls fail-fast until a half-open probe succeeds. Idempotency keys (derived from the tool call ID) prevent duplicate side effects on retries.

**Step 6 — Results flow back.** The result validator scans for injection payloads, checks size (truncating if needed), and projects only the fields the model needs. The context injector assembles `tool_result` blocks and appends them to the conversation. The telemetry sink records a discrete span with tool name, parameters, caller identity, execution duration, and success/failure status.

**Step 7 — Loop or terminate.** The client calls the API again with updated messages. The model either emits more tool calls or returns `stop_reason: "end_turn"`. A hard iteration cap (10-25 tool calls per conversation) prevents infinite loops.

**Two tool categories in Claude.** Client tools (user-defined, bash, text_editor) execute in the user's application; the client must return `tool_result` blocks. Server tools (web_search, web_fetch, code_execution, tool_search) execute on Anthropic's infrastructure; no `tool_result` block is needed.

---

## 2. Core Mechanics & Algorithms

### 2.1 Tool Definition Schema Design

**Anthropic format** — flat, no wrapper:

```json
{
  "name": "get_weather",
  "description": "Get the current weather in a given location",
  "input_schema": {
    "type": "object",
    "properties": {
      "location": { "type": "string", "description": "City and state" }
    },
    "required": ["location"]
  }
}
```

Required fields: `name` (regex `^[a-zA-Z0-9_-]{1,128}$`), `description` (plaintext), `input_schema` (JSON Schema object). Optional: `cache_control`, `strict` (boolean), `defer_loading` (boolean — strips tool from context until discovered via tool search), `allowed_callers` (array restricting which callers can invoke the tool), `input_examples` (array validated against schema; ~20-50 tokens for simple examples, ~100-200 for complex nested objects).

**OpenAI format** — wrapped in `type: "function"`:

```json
{
  "type": "function",
  "name": "get_weather",
  "description": "Retrieves current weather for the given location.",
  "parameters": {
    "type": "object",
    "properties": {
      "location": { "type": "string" },
      "units": { "type": "string", "enum": ["celsius", "fahrenheit"] }
    },
    "required": ["location", "units"],
    "additionalProperties": false
  },
  "strict": true
}
```

OpenAI's `strict: true` requires `additionalProperties: false` on every nested object and all fields listed in `required` (optional fields use `"type": ["string", "null"]`).

**Critical incompatibility.** Copying an OpenAI definition to Anthropic (or vice versa) will silently fail. The `type: "function"` wrapper and `parameters` vs `input_schema` naming differ. Any cross-provider proxy must translate at this boundary.

**Pydantic as the canonical schema layer.** Define a `BaseModel`, call `.model_json_schema()` to generate the JSON Schema for either provider's `parameters`/`input_schema` field. Validate the model's response with `model_validate_json()`. On `ValidationError`, feed the error back to the model for self-correction (cap retries at 3). Add a `schema_version` field for migration.

**`tool_choice` parameter comparison:**

| Behavior | Anthropic | OpenAI |
|---|---|---|
| Model decides | `{"type": "auto"}` (default) | `"auto"` (default) |
| Must use a tool | `{"type": "any"}` | `"required"` |
| Specific tool | `{"type": "tool", "name": "X"}` | `{"type": "function", "name": "X"}` |
| No tools | `{"type": "none"}` | `"none"` |
| Subset restriction | N/A (use tool search + defer_loading) | `{"type": "allowed_tools", "tools": [...]}` |

**Anthropic caveat**: Claude Opus 5.5, Fable 5.1, and Mythos 5.1 do not support forced tool use (`any` or `tool` returns 400). Use `auto` with `strict: true` instead.

### 2.2 Tool Dispatch Lifecycle

**Anthropic lifecycle:**

1. Client sends `POST /v1/messages` with `tools` array and messages.
2. Model responds with `stop_reason: "tool_use"` and one or more `tool_use` content blocks: `{type: "tool_use", id: "toolu_...", name: "...", input: {...}}`.
3. Client executes tool(s) locally.
4. Client appends the assistant response to messages, then appends a `user` message containing `tool_result` blocks: `{type: "tool_result", tool_use_id: "toolu_...", content: "..."}`.
5. Client calls the API again. Model sees results and either emits more tool calls or a final `end_turn` response.
6. Loop until `stop_reason == "end_turn"` or iteration cap reached.

**OpenAI lifecycle (Responses API):**

1. Client sends request with `tools` array.
2. Model returns `function_call` items with `name`, `arguments` (JSON string), `call_id`.
3. Client executes, returns `function_call_output` items referencing each `call_id`.
4. For reasoning models (GPT-5, o4-mini), reasoning items from the model's response must also be passed back with tool call outputs.
5. Loop until no more tool calls.

**OpenAI Chat Completions (legacy):** Uses `choices[0].message.tool_calls` array; results sent as `role: "tool"` messages with matching `tool_call_id`. The Assistants API shuts down August 26, 2026.

### 2.3 Parallel vs Sequential Tool Execution

**OpenAI:** Parallel tool calls enabled by default. The model can return multiple `function_call` items in a single response. Disable with `parallel_tool_calls: false`. Key trade-off: `strict: true` schema guarantees are NOT honored across parallel calls. If schema reliability matters more than latency, disable parallel calls.

**Anthropic:** Claude can return multiple `tool_use` blocks in a single response. Client executes them and returns multiple `tool_result` blocks in a single user message.

**Dependency rule:** Never execute tool calls with side effects (writes, sends, deletes) in parallel with reads that inform them. If Tool B depends on Tool A's result, enforce sequential execution at the orchestration layer, not the model layer.

**Performance data:** The LLMCompiler paper (ICML 2024) showed parallel tool calls reduce end-to-end latency by up to 3.7x.

### 2.4 Streaming with Tool Calls

**Anthropic:** The stream emits typed SSE events. Text deltas, `input_json_delta` fragments for tool call parameters, and thinking blocks arrive interleaved on separate content block indices. Reconstruct by grouping on index, not arrival order. Fine-Grained Tool Streaming (GA February 5, 2026) removes buffering and streams tool parameters immediately as generated rather than waiting for complete valid JSON.

**OpenAI (Responses API):** Key events: `response.output_item.added` (begins a function call, includes name and call_id), `response.function_call_arguments.delta` (incremental JSON chunks), `response.function_call_arguments.done` (complete arguments). Accumulate deltas by `output_index`.

**Cross-provider proxy warning:** Proxying Claude through OpenAI-compatible gateways (LiteLLM, llm-gateway) during streaming can produce invalid tool-use events, empty `tool_use` blocks, or truncated JSON. Use native SDKs where possible.

### 2.5 Dynamic Tool Discovery

**MCP Protocol (2026-07-28 spec).** The 2026-07-28 spec made MCP stateless (request/response), removing protocol-level sessions. Clients discover tools via `tools/list`, execute via `tools/call`. Tool and method names travel in `Mcp-Method` and `Mcp-Name` HTTP headers for gateway routing without body parsing. Dynamic Client Registration (DCR) is deprecated in favor of Client ID Metadata Documents (CIMD). Progressive discovery is planned: servers expose a small entry point and reveal more catalog as the conversation narrows. MCP Server Cards (`.well-known` URLs) are under development for discovery without connecting.

**Anthropic Tool Search Tool.** Enables agents to work with hundreds/thousands of tools. Tools with `defer_loading: true` are stripped from the rendered tools section, preserving prompt cache. When tool search discovers a deferred tool, it returns `tool_reference` blocks (up to 5 per search), which the API expands into full definitions inline. References persist across turns — no re-searching needed.

Token savings benchmark: traditional approach consumes ~77K tokens for 50+ MCP tools; with tool search it drops to ~8.7K tokens (89% reduction).

**OpenAI `allowed_tools`.** The `tool_choice` parameter supports `type: "allowed_tools"` to restrict callable tools to a subset per turn, enabling prompt caching while keeping the full tool list stable.

**Mid-conversation tool changes (beta, 2026-07-01).** Claude Opus 5 supports adding/removing tools mid-conversation through `tool_addition` and `tool_removal` content blocks on `role: "system"` messages, avoiding re-sending the full top-level tools array.

### 2.6 Tool Result Formatting

Return semantic, stable identifiers (slugs, UUIDs) rather than opaque internal references. Include only fields the model needs for its next reasoning step. For void functions (e.g., `send_email`), return a success/failure indicator string. Large results must be truncated or summarized before injection — bloated responses waste context and degrade reasoning quality. OpenAI supports image/file results as an array of objects in `function_call_output`.

### 2.7 Key Invariants

1. **Schema-execution parity.** The schema registered in the tool registry must exactly match the validation logic at the execution boundary. Schema drift causes silent failures.
2. **Idempotent re-execution.** Any tool with side effects must be safe to execute twice with the same inputs (via idempotency keys derived from tool call IDs).
3. **Context monotonicity.** Tool results appended to the conversation only grow the context; they are never silently dropped. Budget enforcement must happen before injection, not after.
4. **Terminal-state clarity.** Every tool response must unambiguously signal completion or continuation. Ambiguous responses ("Found 2 flights, more may be available") cause infinite loops.

---

## 3. Token Economics & NFR Analysis

### 3.1 Cost Formulas

**Token cost per tool definition.** Tool schemas are serialized into the model's input context on every call.

| Tool Complexity | Tokens per Tool (Claude) | Tokens per Tool (OpenAI) |
|---|---|---|
| Minimal (1 param, brief desc) | ~12 tokens | ~15 tokens |
| Production (multi-param, detailed desc) | ~30-65 tokens | ~40-80 tokens |
| Complex (nested objects, enums, examples) | ~80-160 tokens | ~100-200+ tokens |

Claude tokenizes at ~0.8x the rate of GPT-4 (cl100k_base).

**Scaling formula:**

```
Per-request tool overhead = N_tools x avg_tokens_per_tool
```

Example: 20 tools x 60 tokens avg = 1,200 tokens overhead per request.

**Real-world ceiling.** Anthropic's own analysis: a setup with GitHub, Slack, Sentry, and Grafana MCP servers can consume ~55,000 tokens in tool definitions before the model processes a single user instruction. Systems with 50+ tools often hit 50-60% overhead and become economically unsustainable without filtering.

**Cost per request (assumptions: Claude Sonnet 4, $3/M input, $15/M output, Sep 2026):**

```
Tool overhead cost = (1,200 tokens / 1M) x $3 = $0.0036/request
At 10,000 requests/day = $36/day just for tool definitions
With 50+ unfiltered tools (55K tokens): $0.165/request = $1,650/day
```

**Input examples cost-benefit.** ~20-50 tokens per example. First example improves accuracy from ~80% to ~92%; fifth example barely moves the needle. Production systems often use zero examples in schemas.

### 3.2 Cost Optimization Strategies

| Strategy | Token Savings | Implementation Cost |
|---|---|---|
| Dynamic tool gating (20 tools to 1-2 per request) | ~760 tokens/request (40-50% reduction) | Medium — requires classifier or embedding-based selector |
| Tool Search / defer_loading (50+ tools) | 77K to 8.7K tokens (89% reduction) | Low — native API feature |
| Prompt caching (stable tool definitions first) | Cache hit discount (Anthropic: 90% off cached input) | Low — ordering discipline |
| Remove credentials from schemas | 400-600 tokens across 5 tools | Low — vault injection at runtime |
| Cache-friendly ordering (no timestamps in system prompts, stable tool lists) | Prevents cache invalidation | Low — configuration discipline |

**Cache invalidation subtlety.** Changing `tool_choice` invalidates cached message blocks. Tool definitions and system prompts remain cached only if their serialized content is byte-identical.

### 3.3 Latency SLA Targets

Tool-calling workflows exhibit 3-5x higher token usage vs text-only baselines for equivalent tasks. Each round trip adds: model inference latency + tool execution time + network overhead.

| Metric | Single Tool Call | 3-5 Step Chain | Target SLA |
|---|---|---|---|
| p50 latency | 1-2s | 4-8s | < 3s (single), < 10s (chain) |
| p95 latency | 3-5s | 8-15s | < 8s (single), < 20s (chain) |
| p99 latency | 5-10s | 15-30s | < 15s (single), < 45s (chain) |

Parallel tool calls reduce latency by up to 3.7x (LLMCompiler, ICML 2024).

### 3.4 Throughput and Back-Pressure

**Failure amplification in multi-step chains.** LLM API calls fail 1-5% of the time (rate limits, timeouts, server errors). Analysis of LLM API traffic (February 2026): 5% of all LLM call spans reported errors; 60% of those caused by rate limits.

```
P(at least one failure in N-step chain) = 1 - (1 - p)^N

5-step chain, 5% per-step failure rate:
P(failure) = 1 - (0.95)^5 = 22.6%
```

A 5-step tool chain has a ~23% chance of at least one failure per workflow. This makes retry logic and checkpoint/resume patterns non-negotiable for production.

**Back-pressure design.** When the downstream tool execution layer is saturated:
- Queue tool calls with bounded capacity (reject when full, not unbounded growth).
- Return structured backpressure signals to the model: "Tool execution queue is full. Please reduce the number of concurrent tool calls."
- Monitor queue depth as a leading indicator of latency degradation.

### 3.5 BFCL Benchmark Scores

**Berkeley Function Calling Leaderboard (BFCL) v4** (April 2026) evaluation structure: Agentic (40%), Multi-Turn (30%), Live (10%), Non-Live (10%), Hallucination (10%).

**BFCL v3 top scores** (June 2026):

| Model | Score |
|---|---|
| GLM 4.5 | 76.7% |
| Claude Opus 4.7 | 76.6% |
| Gemini 3.1 Flash Lite Preview | 76.5% |

Frontier closed API vs top open-weight model gap: 3-4 percentage points.

**Quality concern.** Epoch AI audit found defects in 48% of a random sample of 50 tasks (from 5,088 scored items). This raises serious questions about benchmark reliability — absolute scores are less informative than relative rankings between models.

**Key finding for interviews.** Models excel at single-turn calls; memory, dynamic decision-making, and long-horizon reasoning remain open challenges. Do not cite BFCL scores as proof of production readiness.

### 3.6 Non-Functional Requirements Summary

| NFR | Requirement | Enforcement Point |
|---|---|---|
| Availability | 99.9% for tool dispatch layer | Circuit breaker + fallback chains |
| Idempotency | All side-effect tools must be safe to re-execute | Idempotency keys from tool_call_id |
| Auditability | Every invocation logged as discrete span | OpenTelemetry / LangSmith |
| Compliance (SOC 2, HIPAA) | Tenant isolation, encrypted logs, access controls | MCP gateway (MintMCP, Bifrost) |
| Cost ceiling | Max token spend per conversation/tenant | Budget guardrail in control plane |
| Latency SLA | p95 < 8s single call, < 20s multi-step | Parallel execution + timeouts |

---

## 4. Distributed Resilience & Security

### 4.1 Retry Logic and Self-Correction

**Error classification matrix** — prerequisite for correct retry strategy:

| Error Type | Owner | Strategy |
|---|---|---|
| Transient (timeout, rate limit, 503) | Infrastructure | Exponential backoff with jitter, max 3-5 retries |
| LLM-recoverable (malformed JSON, wrong params) | Model | Self-correction: return structured error as tool_result |
| User-fixable (missing permissions, invalid input) | User | Surface via interrupt() or equivalent |
| Unexpected (crashes, unhandled exceptions) | Developer | Crash loudly, alert, do not retry |

**Self-correction over blind retries.** If schema validation fails, do not retry with the same prompt. Return the validation error to the model as a `tool_result`. This reduces failure rates by ~40% compared to naive retries. The model can correct its own output when given structured feedback about what went wrong.

**Exponential backoff with jitter** (transient errors only). AWS research shows this reduces retry storms by 60-80%. Critical: implement in the tool execution wrapper, not in the LLM reasoning loop. LLM reasoning is expensive (~$0.05-0.15 per retry at Sonnet pricing) and the model often picks the wrong retry strategy.

### 4.2 Circuit Breakers

Three states: **Closed** (normal operation) -> **Open** (all calls fail-fast for a cooldown period) -> **Half-Open** (allow one test call to check recovery).

Trigger: if a tool fails 3 times in 5 minutes, open the circuit. Do not rely on the model to decide when to stop retrying — enforce at the orchestration layer.

**Layered architecture:**
- **Provider client layer:** handles backoff, Retry-After headers, circuit breaker state. Knows nothing about business logic.
- **Orchestration layer:** handles fallback chains, budget guardrails, saga state management. Knows nothing about HTTP.

### 4.3 Failure Taxonomy

**Hallucinated tools (3-15% of production calls).** Model fabricates tool names, produces malformed arguments, or calls tools with plausible but nonexistent parameter values. Root cause: the model generates tool calls via pattern matching, not database lookups. Some agent workflows fail ~41% of the time.

**Infinite loops.** IAL-Scan examined 6,549 LLM agent repositories and found 68 confirmed infinite agentic loop failures across 47 projects (91.9% precision). This is a systemic design pattern being shipped to production regularly. Causes:
- Ambiguous tool responses ("Found 2 flights, more may be available" — no terminal signal)
- Vague goal definitions ("help the user")
- Model retrying the same failed tool call with identical parameters

Prevention: iteration caps (10-25), per-tool debounce (block third identical call), clear terminal states in tool responses ("SUCCESS: Booking HT79265 confirmed" — reduced tool calls from 14 to 2 in one demo, a 7x improvement), budget guardrails.

**Cascading failures.** A hallucinated output in step 2 becomes a malformed input in step 3. By step 5, the agent operates on a premise that was never true. Especially dangerous for tools with side effects. Five agents at 95% individual accuracy deliver ~77% overall success rate. Multi-agent systems show failure rates between 41% and 86.7% in production.

**Context degradation (silent failure).** Over long sessions, the agent's internal representation of the original task compresses, earlier constraints get deprioritized, and the agent reasons against a progressively incomplete picture. No exception fires. The system reports healthy.

**Model refusing available tools.** The model answers from training data instead of calling a relevant tool. Causes: unclear tool descriptions, training biases, too many tools creating selection ambiguity. Mitigation: `tool_choice: "required"`, detailed descriptions (3-4+ sentences), explicit user-message instructions.

### 4.4 Enterprise Security

**Tool-level RBAC.** Apply Role-Based Access Control at the tool invocation layer. A guest session cannot invoke admin tools; an unauthenticated session cannot access sensitive data. Anthropic's `allowed_callers` restricts which callers can invoke a tool (e.g., only from `code_execution_20260120`). MCP gateways (MintMCP, TrueFoundry) provide SSO, SCIM-driven RBAC, IdP groups, and tool-level allowlisting.

**Input validation and sanitization.** Treat every tool call as untrusted input:
- Schema validation (`strict: true` on both providers)
- Type checking (model sometimes returns `"2"` instead of `2`)
- Enum constraint enforcement (prevents hallucinated parameter values)
- Input normalization (strip zero-width characters, normalize whitespace)
- Range/length validation for numeric and string parameters

**Output validation (indirect prompt injection defense).** OWASP LLM Top 10 ranks prompt injection as LLM01 (2025). May 2026 survey: prompt injection present in 73% of production AI deployments during 2025. Defenses:
- Content segregation: never concatenate raw tool output directly into system prompt without structural delimiter
- Dual-LLM pattern: separate validator model checks tool results before injecting into main model's context
- Output scanning: LLM Guard (Protect AI), Lakera Guard, Azure AI Content Safety
- Typed tool calls via MCP reduce injection surface by structuring data flow

**Execution sandboxing:**
- Docker: per-tool container isolation, resource limits, network policies
- WASM: lightweight isolation, portable, fast cold-start
- Subprocess with cgroups: Linux-level resource isolation
- Anthropic server tools: web_search, code_execution run on Anthropic infrastructure in sandboxed environments

**Audit logging.** Every tool invocation should be a discrete observability span tracking: tool name, parameters, caller identity, tenant ID, execution duration (P50/P95/P99), success/failure status and error type, unique trace IDs (OpenTelemetry / LangSmith), tool call success rate broken down by error type. MCP gateways (Bifrost, MintMCP, TrueFoundry) provide centralized audit logging with SOC 2 Type II and HIPAA alignment.

**Rate limiting per tool.** Different tools have different risk profiles:
- Read-only tools: 100 RPM
- Write tools: 10 RPM
- Destructive tools (delete, transfer): 2 RPM with human approval gates
- Cost attribution per tenant/team enables chargeback and abuse detection

### 4.5 State Management and Idempotency

**Idempotency** is critical for any tool with side effects. If an agent retries, can it safely execute twice? Sending emails, charging cards, inserting rows — all real failure modes. Solutions: idempotency keys (unique per tool call ID), transactional locks, deduplication at execution layer.

**Checkpoint/resume for multi-step workflows.** Multi-step workflows that fail at step 4 and retry from step 1 will re-execute steps 1-3, causing duplicate side effects. Persist completed step results, resume from the last successful step (saga pattern with compensating transactions).

**State management options:**
- **Conversation history as state:** Append all tool calls and results to message array. Simple but grows context linearly.
- **External state store:** Persist intermediate results to DB/cache, pass only references in context. More complex but scalable.
- **LangGraph/workflow engines:** Graph-based state machines with typed state, reducers, and checkpointing.

---

## 5. Production Enterprise Code

### 5.1 Tool Definition with Pydantic Schema Generation

```python
"""Tool definition layer using Pydantic for cross-provider schema generation."""

from __future__ import annotations

import enum
from typing import Any

from pydantic import BaseModel, Field


class TemperatureUnit(str, enum.Enum):
    CELSIUS = "celsius"
    FAHRENHEIT = "fahrenheit"


class GetWeatherInput(BaseModel):
    """Input schema for the get_weather tool."""

    location: str = Field(
        ..., description="City and state, e.g. 'San Francisco, CA'"
    )
    unit: TemperatureUnit = Field(
        default=TemperatureUnit.CELSIUS,
        description="Temperature unit for the response",
    )


def build_anthropic_tool(
    name: str,
    description: str,
    input_model: type[BaseModel],
    *,
    defer_loading: bool = False,
    cache_control: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build an Anthropic-format tool definition from a Pydantic model."""
    tool: dict[str, Any] = {
        "name": name,
        "description": description,
        "input_schema": input_model.model_json_schema(),
    }
    if defer_loading:
        tool["defer_loading"] = True
    if cache_control:
        tool["cache_control"] = cache_control
    return tool


def build_openai_tool(
    name: str,
    description: str,
    input_model: type[BaseModel],
    *,
    strict: bool = True,
) -> dict[str, Any]:
    """Build an OpenAI-format tool definition from a Pydantic model."""
    schema = input_model.model_json_schema()
    if strict:
        schema["additionalProperties"] = False
    return {
        "type": "function",
        "name": name,
        "description": description,
        "parameters": schema,
        "strict": strict,
    }


# Usage:
anthropic_tool = build_anthropic_tool(
    name="get_weather",
    description="Get current weather for a location. Returns temperature, "
    "conditions, and humidity. Use when the user asks about weather.",
    input_model=GetWeatherInput,
    cache_control={"type": "ephemeral"},
)

openai_tool = build_openai_tool(
    name="get_weather",
    description="Get current weather for a location. Returns temperature, "
    "conditions, and humidity. Use when the user asks about weather.",
    input_model=GetWeatherInput,
)
```

### 5.2 Tool Dispatch with Validation and Error Recovery

```python
"""Tool dispatch engine with Pydantic validation and self-correction loop."""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)


class ToolExecutionResult(BaseModel):
    """Standardized result from tool execution."""

    tool_use_id: str
    success: bool
    content: str
    execution_time_ms: float
    error_type: str | None = None


class ToolRegistry:
    """Registry mapping tool names to handlers and input schemas."""

    def __init__(self) -> None:
        self._tools: dict[str, tuple[type[BaseModel], Callable[..., str]]] = {}

    def register(
        self,
        name: str,
        input_model: type[BaseModel],
        handler: Callable[..., str],
    ) -> None:
        self._tools[name] = (input_model, handler)

    def get(self, name: str) -> tuple[type[BaseModel], Callable[..., str]] | None:
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        return name in self._tools

    def list_names(self) -> list[str]:
        return list(self._tools.keys())


def dispatch_tool_call(
    registry: ToolRegistry,
    tool_name: str,
    tool_use_id: str,
    raw_input: dict[str, Any],
) -> ToolExecutionResult:
    """Validate input against schema and execute the tool.

    Returns a structured result suitable for injection as a tool_result block.
    On validation failure, the error message is formatted for model self-correction.
    """
    start = time.monotonic()

    entry = registry.get(tool_name)
    if entry is None:
        return ToolExecutionResult(
            tool_use_id=tool_use_id,
            success=False,
            content=f"Error: Tool '{tool_name}' not found in registry. "
            f"Available tools: {registry.list_names()}",
            execution_time_ms=(time.monotonic() - start) * 1000,
            error_type="hallucinated_tool",
        )

    input_model, handler = entry

    # Validate input against Pydantic schema
    try:
        validated_input = input_model.model_validate(raw_input)
    except ValidationError as exc:
        error_details = json.dumps(exc.errors(), indent=2, default=str)
        return ToolExecutionResult(
            tool_use_id=tool_use_id,
            success=False,
            content=(
                f"Schema validation failed for tool '{tool_name}'.\n"
                f"Errors:\n{error_details}\n\n"
                f"Expected schema:\n"
                f"{json.dumps(input_model.model_json_schema(), indent=2)}\n\n"
                "Please correct the input and try again."
            ),
            execution_time_ms=(time.monotonic() - start) * 1000,
            error_type="validation_error",
        )

    # Execute the tool
    try:
        result_content = handler(**validated_input.model_dump())
    except TimeoutError:
        return ToolExecutionResult(
            tool_use_id=tool_use_id,
            success=False,
            content=f"Tool '{tool_name}' execution timed out. "
            "Consider a more specific query or breaking the request into "
            "smaller parts.",
            execution_time_ms=(time.monotonic() - start) * 1000,
            error_type="timeout",
        )
    except Exception as exc:
        logger.exception("Tool execution failed: %s", tool_name)
        return ToolExecutionResult(
            tool_use_id=tool_use_id,
            success=False,
            content=f"Tool '{tool_name}' execution error: {type(exc).__name__}: {exc}",
            execution_time_ms=(time.monotonic() - start) * 1000,
            error_type="execution_error",
        )

    elapsed_ms = (time.monotonic() - start) * 1000
    logger.info(
        "tool_execution",
        extra={
            "tool_name": tool_name,
            "tool_use_id": tool_use_id,
            "success": True,
            "execution_time_ms": elapsed_ms,
        },
    )

    return ToolExecutionResult(
        tool_use_id=tool_use_id,
        success=True,
        content=result_content,
        execution_time_ms=elapsed_ms,
    )
```

### 5.3 Retry Logic with Exponential Backoff and Self-Correction

```python
"""Retry logic for transient failures and model self-correction loop.

Transient errors (timeouts, rate limits, 503s) use exponential backoff at the
infrastructure layer. Schema/validation errors use model self-correction at the
orchestration layer. These are separate concerns and must not be conflated.
"""

from __future__ import annotations

import random
import time
import logging
from typing import Any, Callable, TypeVar

import httpx

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Transient HTTP status codes that warrant retry
RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}


def retry_with_backoff(
    fn: Callable[..., T],
    *args: Any,
    max_retries: int = 4,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    **kwargs: Any,
) -> T:
    """Execute a function with exponential backoff and full jitter.

    Used for transient infrastructure errors (network, rate limit, server).
    NOT for LLM-recoverable errors -- those go through self-correction.

    Jitter formula (AWS "Full Jitter"): sleep = random(0, min(cap, base * 2^attempt))
    This reduces retry storms by 60-80% compared to fixed delays.
    """
    last_exception: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            return fn(*args, **kwargs)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in RETRYABLE_STATUS_CODES:
                raise  # Non-retryable HTTP error
            last_exception = exc

            # Respect Retry-After header if present
            retry_after = exc.response.headers.get("Retry-After")
            if retry_after:
                delay = float(retry_after)
            else:
                delay = random.uniform(0, min(max_delay, base_delay * (2**attempt)))

            logger.warning(
                "Retryable HTTP error (attempt %d/%d): status=%d, delay=%.2fs",
                attempt + 1,
                max_retries + 1,
                exc.response.status_code,
                delay,
            )
            time.sleep(delay)
        except (httpx.ConnectTimeout, httpx.ReadTimeout, ConnectionError) as exc:
            last_exception = exc
            delay = random.uniform(0, min(max_delay, base_delay * (2**attempt)))
            logger.warning(
                "Transient network error (attempt %d/%d): %s, delay=%.2fs",
                attempt + 1,
                max_retries + 1,
                type(exc).__name__,
                delay,
            )
            time.sleep(delay)

    raise last_exception  # type: ignore[misc]


def self_correction_loop(
    llm_call: Callable[[list[dict[str, Any]]], dict[str, Any]],
    messages: list[dict[str, Any]],
    dispatch_fn: Callable[[str, str, dict[str, Any]], "ToolExecutionResult"],
    *,
    max_corrections: int = 3,
    max_iterations: int = 25,
) -> list[dict[str, Any]]:
    """Run the agentic tool-calling loop with self-correction on validation errors.

    When a tool call fails validation, the error is returned to the model as a
    tool_result so it can correct its output. Blind retries (re-sending the same
    prompt) are never used for validation errors.

    Args:
        llm_call: Function that sends messages to the LLM and returns the response.
        messages: Conversation history.
        dispatch_fn: Function that validates and executes a tool call.
        max_corrections: Max consecutive self-correction attempts per tool.
        max_iterations: Hard cap on total tool-calling rounds.
    """
    correction_counts: dict[str, int] = {}  # tool_use_id -> correction count

    for iteration in range(max_iterations):
        response = llm_call(messages)
        messages.append({"role": "assistant", "content": response["content"]})

        # Check if the model is done
        if response.get("stop_reason") == "end_turn":
            break

        # Process tool calls
        tool_results: list[dict[str, Any]] = []
        for block in response["content"]:
            if block.get("type") != "tool_use":
                continue

            tool_use_id = block["id"]
            result = dispatch_fn(block["name"], tool_use_id, block["input"])

            if not result.success and result.error_type == "validation_error":
                count = correction_counts.get(tool_use_id, 0) + 1
                correction_counts[tool_use_id] = count
                if count > max_corrections:
                    result = ToolExecutionResult(
                        tool_use_id=tool_use_id,
                        success=False,
                        content=f"Tool '{block['name']}' failed validation "
                        f"{max_corrections} times. Skipping this tool call. "
                        "Please proceed without it or use a different approach.",
                        execution_time_ms=0,
                        error_type="max_corrections_exceeded",
                    )

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": result.content,
                "is_error": not result.success,
            })

        if tool_results:
            messages.append({"role": "user", "content": tool_results})

    return messages
```

### 5.4 Circuit Breaker for External Tool Calls

```python
"""Circuit breaker implementation for unreliable external tools.

Three states:
  CLOSED  -> normal operation; failures increment counter
  OPEN    -> all calls fail-fast; no external calls made
  HALF_OPEN -> allow one probe call to test recovery

Transition rules:
  CLOSED -> OPEN: failure_count >= failure_threshold within window
  OPEN -> HALF_OPEN: cooldown_seconds elapsed
  HALF_OPEN -> CLOSED: probe call succeeds
  HALF_OPEN -> OPEN: probe call fails (reset cooldown)
"""

from __future__ import annotations

import enum
import logging
import threading
import time
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CircuitState(enum.Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    """Raised when a call is attempted on an open circuit."""

    def __init__(self, tool_name: str, retry_after: float) -> None:
        self.tool_name = tool_name
        self.retry_after = retry_after
        super().__init__(
            f"Circuit breaker OPEN for tool '{tool_name}'. "
            f"Retry after {retry_after:.1f}s."
        )


class CircuitBreaker:
    """Per-tool circuit breaker with thread-safe state transitions."""

    def __init__(
        self,
        tool_name: str,
        failure_threshold: int = 3,
        window_seconds: float = 300.0,
        cooldown_seconds: float = 60.0,
    ) -> None:
        self.tool_name = tool_name
        self.failure_threshold = failure_threshold
        self.window_seconds = window_seconds
        self.cooldown_seconds = cooldown_seconds

        self._state = CircuitState.CLOSED
        self._failure_timestamps: list[float] = []
        self._last_opened_at: float = 0.0
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            if self._state == CircuitState.OPEN:
                elapsed = time.monotonic() - self._last_opened_at
                if elapsed >= self.cooldown_seconds:
                    self._state = CircuitState.HALF_OPEN
                    logger.info(
                        "Circuit HALF_OPEN for '%s' after %.1fs cooldown",
                        self.tool_name,
                        elapsed,
                    )
            return self._state

    def call(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Execute fn through the circuit breaker."""
        current_state = self.state

        if current_state == CircuitState.OPEN:
            retry_after = self.cooldown_seconds - (
                time.monotonic() - self._last_opened_at
            )
            raise CircuitOpenError(self.tool_name, max(0, retry_after))

        try:
            result = fn(*args, **kwargs)
        except Exception as exc:
            self._record_failure()
            raise
        else:
            self._record_success()
            return result

    def _record_failure(self) -> None:
        with self._lock:
            now = time.monotonic()
            self._failure_timestamps.append(now)

            # Prune failures outside the window
            cutoff = now - self.window_seconds
            self._failure_timestamps = [
                t for t in self._failure_timestamps if t > cutoff
            ]

            if self._state == CircuitState.HALF_OPEN:
                # Probe failed -- reopen
                self._state = CircuitState.OPEN
                self._last_opened_at = now
                logger.warning(
                    "Circuit re-OPENED for '%s' (probe failed)", self.tool_name
                )
            elif len(self._failure_timestamps) >= self.failure_threshold:
                self._state = CircuitState.OPEN
                self._last_opened_at = now
                logger.warning(
                    "Circuit OPENED for '%s' (%d failures in %.0fs)",
                    self.tool_name,
                    len(self._failure_timestamps),
                    self.window_seconds,
                )

    def _record_success(self) -> None:
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.CLOSED
                self._failure_timestamps.clear()
                logger.info(
                    "Circuit CLOSED for '%s' (probe succeeded)", self.tool_name
                )


# Usage with the dispatch engine:
class ProtectedToolDispatcher:
    """Wraps tool dispatch with per-tool circuit breakers."""

    def __init__(self) -> None:
        self._breakers: dict[str, CircuitBreaker] = {}

    def get_breaker(self, tool_name: str) -> CircuitBreaker:
        if tool_name not in self._breakers:
            self._breakers[tool_name] = CircuitBreaker(tool_name)
        return self._breakers[tool_name]

    def dispatch(
        self,
        registry: "ToolRegistry",
        tool_name: str,
        tool_use_id: str,
        raw_input: dict[str, Any],
    ) -> "ToolExecutionResult":
        breaker = self.get_breaker(tool_name)
        try:
            return breaker.call(
                dispatch_tool_call, registry, tool_name, tool_use_id, raw_input
            )
        except CircuitOpenError as exc:
            return ToolExecutionResult(
                tool_use_id=tool_use_id,
                success=False,
                content=str(exc),
                execution_time_ms=0,
                error_type="circuit_open",
            )
```

### 5.5 Dynamic Tool Registry with Progressive Disclosure

```python
"""Dynamic tool registry supporting defer_loading and progressive disclosure.

Tools marked as deferred are excluded from the rendered tools array sent to
the LLM, preserving prompt cache and reducing token overhead. They are loaded
on demand when discovered via tool search.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel


class ToolRegistryEntry(BaseModel):
    """Metadata for a registered tool."""

    name: str
    description: str
    input_model_schema: dict[str, Any]
    category: str = "general"
    tags: list[str] = []
    deferred: bool = False
    schema_version: str = "1.0.0"


class DynamicToolRegistry:
    """Registry supporting progressive disclosure and deferred loading.

    Keeps 3-5 frequently used tools always loaded (non-deferred) and defers
    the rest. Tool selection accuracy degrades with 30+ tools loaded at once.
    """

    def __init__(self, max_active_tools: int = 10) -> None:
        self._entries: dict[str, ToolRegistryEntry] = {}
        self._handlers: dict[str, Any] = {}
        self._active_tools: set[str] = set()
        self.max_active_tools = max_active_tools

    def register(
        self,
        name: str,
        description: str,
        input_model: type[BaseModel],
        handler: Any,
        *,
        category: str = "general",
        tags: list[str] | None = None,
        deferred: bool = False,
    ) -> None:
        self._entries[name] = ToolRegistryEntry(
            name=name,
            description=description,
            input_model_schema=input_model.model_json_schema(),
            category=category,
            tags=tags or [],
            deferred=deferred,
        )
        self._handlers[name] = handler
        if not deferred:
            self._active_tools.add(name)

    def get_active_tool_definitions(
        self, provider: str = "anthropic"
    ) -> list[dict[str, Any]]:
        """Return tool definitions for non-deferred tools only.

        These are serialized into the LLM's input context on every call.
        Deferred tools are excluded to preserve prompt cache.
        """
        definitions = []
        for name in self._active_tools:
            entry = self._entries[name]
            if provider == "anthropic":
                definitions.append({
                    "name": entry.name,
                    "description": entry.description,
                    "input_schema": entry.input_model_schema,
                })
            elif provider == "openai":
                schema = dict(entry.input_model_schema)
                schema["additionalProperties"] = False
                definitions.append({
                    "type": "function",
                    "name": entry.name,
                    "description": entry.description,
                    "parameters": schema,
                    "strict": True,
                })
        return definitions

    def search_tools(
        self, query: str, max_results: int = 5
    ) -> list[ToolRegistryEntry]:
        """Search across all tools (including deferred) by name, description, tags.

        Uses simple keyword matching. Production systems would use BM25 or
        embedding-based search for better recall.
        """
        query_lower = query.lower()
        query_terms = re.split(r"\s+", query_lower)
        scored: list[tuple[float, ToolRegistryEntry]] = []

        for entry in self._entries.values():
            searchable = (
                f"{entry.name} {entry.description} {' '.join(entry.tags)}"
            ).lower()
            score = sum(1.0 for term in query_terms if term in searchable)
            # Boost exact name match
            if query_lower in entry.name.lower():
                score += 3.0
            if score > 0:
                scored.append((score, entry))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [entry for _, entry in scored[:max_results]]

    def activate_tool(self, name: str) -> bool:
        """Load a deferred tool into the active set (progressive disclosure)."""
        if name not in self._entries:
            return False
        if len(self._active_tools) >= self.max_active_tools:
            # Evict least-recently-used or lowest-priority tool
            # (simplified: just refuse)
            return False
        self._active_tools.add(name)
        return True

    def deactivate_tool(self, name: str) -> None:
        """Remove a tool from the active set without unregistering it."""
        self._active_tools.discard(name)

    def get_deferred_tool_names(self) -> list[str]:
        """List all deferred tools (available via search but not loaded)."""
        return [
            name
            for name, entry in self._entries.items()
            if entry.deferred and name not in self._active_tools
        ]
```

### 5.6 Sandboxed Tool Execution

```python
"""Sandboxed tool execution using subprocess with resource limits.

Runs untrusted tool code in an isolated subprocess with CPU time, memory,
and wall-clock time limits. For stronger isolation, swap subprocess for
Docker (container) or WASM (wasmtime) runtimes.
"""

from __future__ import annotations

import json
import logging
import resource
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class SandboxConfig:
    """Resource limits for sandboxed execution."""

    def __init__(
        self,
        timeout_seconds: int = 30,
        max_memory_mb: int = 256,
        max_cpu_seconds: int = 10,
        allowed_modules: list[str] | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_memory_mb = max_memory_mb
        self.max_cpu_seconds = max_cpu_seconds
        self.allowed_modules = allowed_modules or []


class SandboxResult:
    """Result from sandboxed execution."""

    def __init__(
        self,
        success: bool,
        output: str,
        error: str | None = None,
        exit_code: int = 0,
    ) -> None:
        self.success = success
        self.output = output
        self.error = error
        self.exit_code = exit_code


def _build_sandbox_script(
    tool_code: str, input_data: dict[str, Any], max_memory_mb: int, max_cpu_s: int
) -> str:
    """Build a self-contained Python script with resource limits baked in."""
    return f"""
import json
import resource
import sys

# Set resource limits
resource.setrlimit(resource.RLIMIT_AS, ({max_memory_mb * 1024 * 1024}, {max_memory_mb * 1024 * 1024}))
resource.setrlimit(resource.RLIMIT_CPU, ({max_cpu_s}, {max_cpu_s}))

input_data = json.loads('''{json.dumps(input_data)}''')

{tool_code}

try:
    result = execute(input_data)
    print(json.dumps({{"success": True, "output": str(result)}}))
except Exception as e:
    print(json.dumps({{"success": False, "error": f"{{type(e).__name__}}: {{e}}"}}))
"""


def execute_in_sandbox(
    tool_code: str,
    input_data: dict[str, Any],
    config: SandboxConfig | None = None,
) -> SandboxResult:
    """Execute tool code in an isolated subprocess with resource limits.

    The tool_code must define an `execute(input_data: dict) -> str` function.

    Args:
        tool_code: Python source code defining an execute() function.
        input_data: Validated input dictionary passed to execute().
        config: Resource limits. Defaults to 30s timeout, 256MB memory.
    """
    if config is None:
        config = SandboxConfig()

    script = _build_sandbox_script(
        tool_code, input_data, config.max_memory_mb, config.max_cpu_seconds
    )

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=True, dir="/tmp"
    ) as f:
        f.write(script)
        f.flush()

        try:
            proc = subprocess.run(
                [sys.executable, f.name],
                capture_output=True,
                text=True,
                timeout=config.timeout_seconds,
                env={"PATH": "/usr/bin:/bin"},  # Minimal PATH
            )
        except subprocess.TimeoutExpired:
            logger.warning("Sandbox execution timed out after %ds", config.timeout_seconds)
            return SandboxResult(
                success=False,
                output="",
                error=f"Execution timed out after {config.timeout_seconds}s",
                exit_code=-1,
            )

    if proc.returncode != 0:
        return SandboxResult(
            success=False,
            output=proc.stdout,
            error=proc.stderr or "Non-zero exit code",
            exit_code=proc.returncode,
        )

    try:
        result_data = json.loads(proc.stdout)
        return SandboxResult(
            success=result_data.get("success", False),
            output=result_data.get("output", ""),
            error=result_data.get("error"),
            exit_code=0,
        )
    except json.JSONDecodeError:
        return SandboxResult(
            success=False,
            output=proc.stdout,
            error="Failed to parse sandbox output as JSON",
            exit_code=proc.returncode,
        )
```

### 5.7 Structured Logging for Tool Observability

```python
"""Structured logging for tool invocation observability.

Each tool invocation is emitted as a discrete structured log entry compatible
with OpenTelemetry span semantics. Captures: tool name, parameters, caller
identity, tenant ID, execution duration, success/failure, and error type.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from typing import Any, Generator


@dataclass
class ToolInvocationSpan:
    """Discrete observability span for a single tool invocation."""

    trace_id: str
    span_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    tool_name: str = ""
    tool_use_id: str = ""
    caller_identity: str = ""
    tenant_id: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    start_time: float = 0.0
    end_time: float = 0.0
    duration_ms: float = 0.0
    success: bool = False
    error_type: str | None = None
    error_message: str | None = None
    result_size_bytes: int = 0
    circuit_breaker_state: str = "closed"


class ToolObservabilityLogger:
    """Structured logger for tool invocation spans.

    Emits JSON-formatted log entries that can be ingested by OpenTelemetry
    collectors, Datadog, or any structured logging pipeline.
    """

    def __init__(self, service_name: str = "tool-gateway") -> None:
        self._logger = logging.getLogger(f"{service_name}.tool_invocation")
        self._service_name = service_name

    @contextmanager
    def span(
        self,
        trace_id: str,
        tool_name: str,
        tool_use_id: str,
        parameters: dict[str, Any],
        caller_identity: str = "",
        tenant_id: str = "",
    ) -> Generator[ToolInvocationSpan, None, None]:
        """Context manager that tracks timing and emits a structured log on exit."""
        invocation = ToolInvocationSpan(
            trace_id=trace_id,
            tool_name=tool_name,
            tool_use_id=tool_use_id,
            caller_identity=caller_identity,
            tenant_id=tenant_id,
            parameters=_sanitize_params(parameters),
            start_time=time.time(),
        )

        try:
            yield invocation
        except Exception as exc:
            invocation.success = False
            invocation.error_type = type(exc).__name__
            invocation.error_message = str(exc)[:500]
            raise
        finally:
            invocation.end_time = time.time()
            invocation.duration_ms = (
                invocation.end_time - invocation.start_time
            ) * 1000
            self._emit(invocation)

    def _emit(self, span: ToolInvocationSpan) -> None:
        log_data = {
            "event": "tool_invocation",
            "service": self._service_name,
            **asdict(span),
        }
        if span.success:
            self._logger.info(json.dumps(log_data, default=str))
        else:
            self._logger.warning(json.dumps(log_data, default=str))


def _sanitize_params(params: dict[str, Any]) -> dict[str, Any]:
    """Remove sensitive fields from parameters before logging."""
    sensitive_keys = {"password", "token", "secret", "api_key", "credential"}
    return {
        k: "***REDACTED***" if k.lower() in sensitive_keys else v
        for k, v in params.items()
    }


# Usage:
# obs = ToolObservabilityLogger(service_name="my-agent")
# with obs.span(
#     trace_id="abc123",
#     tool_name="get_weather",
#     tool_use_id="toolu_01",
#     parameters={"location": "SF"},
#     caller_identity="user:42",
#     tenant_id="tenant:acme",
# ) as span:
#     result = execute_tool(...)
#     span.success = True
#     span.result_size_bytes = len(result)
```

---

## 6. Architectural System Design Scenarios

### 6.1 Scenario: Enterprise Tool Gateway for Multi-Team AI Platform

**Problem statement.** A financial services company with 12 product teams is deploying LLM-powered agents across customer support, compliance, and internal operations. Each team builds its own tools (database queries, CRM lookups, document generators, payment APIs). Requirements: centralized governance, per-team cost attribution, SOC 2 audit trail, tool-level access control, and sub-3-second p95 latency for single tool calls. The platform must support 200+ tools without degrading model accuracy or blowing up token costs.

**Proposed architecture:**

```
┌────────────────────────────────────────────────────────────────────────┐
│                         API GATEWAY (Kong/Envoy)                       │
│  JWT validation │ TLS termination │ Global rate limit │ Request routing │
└────────────────────────────────────┬───────────────────────────────────┘
                                     │
                                     ▼
┌────────────────────────────────────────────────────────────────────────┐
│                        TOOL GATEWAY SERVICE                            │
│                                                                        │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────┐                 │
│  │ RBAC Engine  │  │ Tool Registry│  │ Token Budget   │                 │
│  │ (SCIM sync   │  │ (200+ tools, │  │ Controller     │                 │
│  │  from IdP)   │  │  versioned)  │  │ (per-team cap) │                 │
│  └──────┬──────┘  └──────┬───────┘  └───────┬───────┘                 │
│         │                │                   │                          │
│  ┌──────┴──────────────────┴───────────────────┴──────┐                │
│  │              Tool Selection Engine                  │                │
│  │  • Filter: RBAC -> 200 tools to team's authorized  │                │
│  │    subset (~15-30 tools)                            │                │
│  │  • Gate: embedding-based relevance -> 2-5 tools    │                │
│  │  • Defer: remaining tools discoverable via search   │                │
│  └────────────────────────┬───────────────────────────┘                │
│                           │                                             │
│  ┌────────────────────────┴───────────────────────────┐                │
│  │           Dispatch + Execution Layer                │                │
│  │  Circuit breakers │ Sandboxed execution │ Retries   │                │
│  │  Idempotency keys │ Credential injection │ Timeout  │                │
│  └────────────────────────┬───────────────────────────┘                │
│                           │                                             │
│  ┌────────────────────────┴───────────────────────────┐                │
│  │         Observability + Audit Pipeline              │                │
│  │  OpenTelemetry traces │ Per-tool SLIs │ Cost meter  │                │
│  │  SOC 2 audit log │ Alerting (PagerDuty)             │                │
│  └────────────────────────────────────────────────────┘                │
└────────────────────────────────────────────────────────────────────────┘
                                     │
                    ┌────────────────┼────────────────┐
                    ▼                ▼                ▼
             ┌───────────┐  ┌───────────────┐  ┌──────────────┐
             │ MCP Servers│  │ REST API      │  │ Internal     │
             │ (stateless │  │ Connectors    │  │ gRPC Services│
             │  2026-07-28│  │ (CRM, payment)│  │ (compliance) │
             │  spec)     │  │               │  │              │
             └───────────┘  └───────────────┘  └──────────────┘
```

**Technology choices:** Kong or Envoy for API gateway (existing finserv infra). Tool gateway as a Go or Python service behind the gateway. PostgreSQL for tool registry with versioned schemas. Redis for circuit breaker state and rate limit counters. HashiCorp Vault for credential injection. OpenTelemetry collector -> Datadog for observability. SCIM sync from Okta for RBAC.

**Trade-off evaluation matrix:**

| Dimension | A: Centralized Gateway (recommended) | B: Per-Team Sidecar Proxies | C: SDK-Only (no gateway) |
|---|---|---|---|
| **Cost** | Medium — one service to operate and scale | High — N sidecars, N configs, N upgrades | Low — no infra, SDK overhead only |
| **Latency overhead** | +5-15ms per call (network hop) | +2-5ms (co-located) | ~0ms (in-process) |
| **Ops complexity** | Medium — single deployment, centralized config | High — distributed config, version skew risk | Low — nothing to deploy |
| **Security posture** | Strong — single enforcement point, consistent audit | Medium — each sidecar must be correctly configured | Weak — no central policy, audit gaps |
| **Scalability ceiling** | High — horizontal scaling, load balancing | Medium — bounded by pod resources | High — scales with app |
| **Compliance (SOC 2)** | Strong — centralized audit log, SCIM-driven RBAC | Achievable — requires log aggregation | Difficult — no central audit trail |

**Decision rationale.** The centralized gateway wins because: (1) SOC 2 compliance requires a single, auditable enforcement point for tool access — distributing this across sidecars creates compliance gaps and audit log fragmentation; (2) the +5-15ms latency overhead is well within the 3-second p95 target given that LLM inference itself takes 500-2000ms; (3) 200+ tools with per-team RBAC and cost attribution is operationally intractable without centralized registry and metering; (4) the tool selection engine (RBAC filter -> embedding gate -> defer remainder) keeps per-request token overhead to 2-5 tools (~120-325 tokens) regardless of total tool count, solving the token economics problem.

---

### 6.2 Scenario: Self-Healing Agent Workflow for Order Processing

**Problem statement.** An e-commerce company processes 50,000 orders/day through an LLM agent that handles: inventory check, payment authorization, shipping label generation, and customer notification. Each step calls a different external API. The agent must handle partial failures gracefully — a payment that was authorized but whose shipping label generation failed must not re-authorize payment on retry. Current failure rate: 23% per 4-step workflow (compound 5% per-step failure). Target: <2% end-to-end failure rate with zero duplicate charges.

**Proposed architecture:**

```
┌────────────────────────────────────────────────────────────────────────┐
│                     ORCHESTRATION LAYER                                │
│                                                                        │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │                     Saga Coordinator                              │  │
│  │  • Step execution with checkpoint after each success              │  │
│  │  • Compensating transactions on failure (reverse-charge, etc.)    │  │
│  │  • Resume from last checkpoint on retry                           │  │
│  └─────────────────────────────┬────────────────────────────────────┘  │
│                                │                                       │
│  ┌─────────────┬───────────────┼───────────────┬───────────────┐      │
│  ▼             ▼               ▼               ▼               │      │
│  Step 1        Step 2          Step 3          Step 4           │      │
│  Inventory     Payment         Shipping        Notification    │      │
│  Check         Auth            Label           Email/SMS       │      │
│  ─────────     ────────        ────────        ────────────    │      │
│  Idempotent    Idemp. key:     Idemp. key:     Idemp. key:    │      │
│  (read-only)   order_id+ts     order_id+ts     order_id+ts    │      │
│                Compensate:     Compensate:     Compensate:     │      │
│                void_auth()     cancel_label()  (no-op)         │      │
│  ┌─────────┐  ┌─────────┐    ┌─────────┐    ┌─────────┐      │      │
│  │ Circuit  │  │ Circuit  │    │ Circuit  │    │ Circuit  │      │      │
│  │ Breaker  │  │ Breaker  │    │ Breaker  │    │ Breaker  │      │      │
│  └─────────┘  └─────────┘    └─────────┘    └─────────┘      │      │
│       │             │              │              │             │      │
└───────┼─────────────┼──────────────┼──────────────┼────────────┘      │
        ▼             ▼              ▼              ▼                    │
  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐               │
  │Inventory │  │Payment   │  │Shipping  │  │Notific.  │               │
  │API       │  │Gateway   │  │API       │  │Service   │               │
  └──────────┘  └──────────┘  └──────────┘  └──────────┘               │
                                                                        │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │                   State Store (PostgreSQL)                        │  │
│  │  • Saga state: {order_id, current_step, completed_steps[],       │  │
│  │    step_results{}, compensation_needed[], status}                 │  │
│  │  • Idempotency log: {key, result, created_at, expires_at}        │  │
│  └──────────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────────┘
```

**Technology choices:** Python orchestration service (or LangGraph with typed state). PostgreSQL for saga state and idempotency log (ACID guarantees). Per-step circuit breakers (3 failures in 5 min -> open). Exponential backoff with jitter for transient errors only. Self-correction loop for the LLM's tool call parameter errors. Dead-letter queue (SQS/RabbitMQ) for orders that exhaust retries.

**Trade-off evaluation matrix:**

| Dimension | A: Saga with Checkpoints (recommended) | B: Simple Retry from Start | C: Event-Driven Choreography |
|---|---|---|---|
| **Cost** | Medium — state store + coordinator | Low — stateless, no persistence | Medium — message broker + consumers |
| **Duplicate side effects** | Zero — idempotency keys + checkpoint resume | High — re-executes all prior steps | Low — per-consumer idempotency |
| **Latency on retry** | Low — resumes from last checkpoint | High — repeats successful steps | Medium — re-queues from failure point |
| **Ops complexity** | Medium — saga state management, compensations | Low — nothing to manage | High — distributed tracing, event schemas, ordering |
| **Failure rate** | <2% — targeted retries, circuit breakers | ~23% — compound failures, no isolation | <5% — per-step isolation, but event ordering issues |
| **Debuggability** | High — full saga state visible, step-by-step history | Low — no intermediate state | Medium — requires distributed trace correlation |

**Decision rationale.** The saga pattern with checkpoints wins because: (1) the zero-duplicate-charges requirement is non-negotiable for financial operations — simple retry-from-start violates this by re-executing payment authorization; (2) checkpoint resume drops retry latency from 4-step re-execution (~8-12s) to single-step retry (~2-3s); (3) the compound failure rate drops from 23% to <2% because only the failed step is retried (with circuit breaker preventing repeated calls to a down service); (4) compensating transactions provide a clean rollback path when step 3 fails after step 2 succeeds (void the payment auth rather than leaving orphaned authorizations); (5) event-driven choreography, while elegant, introduces event ordering challenges and distributed state that are harder to reason about in a financial context where correctness trumps decoupling.

**Reliability math validation:**

```
Without saga:  P(failure) = 1 - (0.95)^4 = 18.5%
With saga + per-step retry (3 attempts):
  P(step failure after retries) = (0.05)^3 = 0.000125
  P(workflow failure) = 1 - (1 - 0.000125)^4 = 0.05%
With circuit breaker (fail-fast when service is down):
  Avoids wasting retries on known-down services
  Effective failure rate: <2% (accounts for non-retryable failures)
```

---

## Quick Reference: Interview Talking Points

1. **Tool calling is an orchestration problem, not a model problem.** The model emits structured requests; everything else (validation, execution, retry, security) lives in the orchestration layer you build.

2. **Token economics dominate at scale.** 200+ tools can consume 55K+ tokens before the model sees a user message. Dynamic gating (embedding-based selection) and defer_loading (89% token reduction) are not optimizations — they are prerequisites.

3. **Compound failure rates are the hidden killer.** A 5-step chain with 5% per-step failure rate yields 23% end-to-end failure. Saga patterns with checkpoint/resume and per-step circuit breakers bring this below 2%.

4. **Self-correction beats blind retry.** Returning structured validation errors to the model reduces failure rates by ~40% compared to naive retries. Exponential backoff is for infrastructure errors only.

5. **Security is a layer, not a feature.** Tool-level RBAC, input/output validation, execution sandboxing, and audit logging are separate concerns enforced at different points in the architecture. OWASP LLM01 (prompt injection) remains the top risk.

6. **BFCL scores are directional, not definitive.** 48% defect rate in the benchmark's own tasks. Use relative rankings between models, not absolute scores, when making model selection decisions.

7. **MCP 2026-07-28 is stateless.** No protocol-level sessions. Tool and method names in HTTP headers for gateway routing. This simplifies gateway architecture but requires the application layer to manage state.
