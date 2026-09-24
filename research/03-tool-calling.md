# Research: Tool Calling

**Date researched**: 2026-09-23
**Sources consulted**: 38

---

## 1. System Topology & Mechanics

### 1.1 Tool Definition Schemas: Anthropic vs OpenAI

**Anthropic (Claude) format** -- flat, no wrapper:

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

Required fields: `name` (regex `^[a-zA-Z0-9_-]{1,128}$`), `description` (plaintext), `input_schema` (JSON Schema object). Optional fields: `cache_control`, `strict` (boolean, enables schema validation), `defer_loading` (boolean, strips tool from context until discovered via tool search), `allowed_callers` (array restricting which callers can invoke the tool), `input_examples` (array of example inputs, validated against schema -- ~20-50 tokens for simple examples, ~100-200 tokens for complex nested objects). ([Anthropic define-tools docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools))

**OpenAI format** -- wrapped in `type: "function"`:

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

Key differences: OpenAI wraps in `type: "function"`, uses `parameters` instead of `input_schema`. OpenAI also supports `namespace` grouping and `custom` tools (free-form text input with optional context-free grammar constraints via Lark/regex). OpenAI's `strict: true` requires `additionalProperties: false` on every object and all fields listed in `required` (optional fields use `"type": ["string", "null"]`). ([OpenAI function calling guide](https://developers.openai.com/api/docs/guides/function-calling))

**Critical incompatibility**: Copying an OpenAI tool definition directly to Anthropic (or vice versa) will silently fail or produce validation errors. The `type: "function"` wrapper and `parameters` vs `input_schema` naming differ.

### 1.2 Tool Dispatch Lifecycle

**Anthropic lifecycle**:
1. Client sends `POST /v1/messages` with `tools` array and messages
2. Model responds with `stop_reason: "tool_use"` and one or more `tool_use` content blocks (`{type: "tool_use", id: "toolu_...", name: "...", input: {...}}`)
3. Client executes tool(s) locally
4. Client appends the assistant response to messages, then appends a `user` message containing `tool_result` blocks: `{type: "tool_result", tool_use_id: "toolu_...", content: "..."}`
5. Client calls the API again; model sees results and either emits more tool calls or a final `end_turn` response
6. Loop until `stop_reason == "end_turn"` or iteration cap reached

**Two tool categories in Claude**: Client tools (user-defined, bash, text_editor) run in the user's application. Server tools (web_search, web_fetch, code_execution, tool_search) run on Anthropic's infrastructure -- no `tool_result` block needed. ([Anthropic tool use overview](https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview))

**OpenAI lifecycle (Responses API)**:
1. Client sends request with `tools` array
2. Model returns `function_call` items with `name`, `arguments` (JSON string), `call_id`
3. Client executes, returns `function_call_output` items referencing each `call_id`
4. For reasoning models (GPT-5, o4-mini), reasoning items from the model's response must also be passed back with tool call outputs
5. Loop until no more tool calls

**OpenAI lifecycle (Chat Completions -- legacy)**: Uses `choices[0].message.tool_calls` array; results sent as `role: "tool"` messages with matching `tool_call_id`. The Assistants API shuts down August 26, 2026. ([OpenAI function calling guide](https://developers.openai.com/api/docs/guides/function-calling))

### 1.3 Parallel vs Sequential Tool Calling

**OpenAI**: Parallel tool calls are enabled by default. The model can return multiple `function_call` items in a single response. Disable with `parallel_tool_calls: false`. Key trade-off: `strict: true` schema guarantees are NOT honored across parallel calls. If schema reliability matters more than latency, disable parallel calls. The LLMCompiler paper (ICML 2024) showed parallel tool calls reduce end-to-end latency by up to 3.7x. ([OpenAI function calling guide](https://developers.openai.com/api/docs/guides/function-calling))

**Anthropic**: Claude can return multiple `tool_use` blocks in a single response. Client executes them and returns multiple `tool_result` blocks in a single user message. The model sees all results at once.

**Dependency handling**: Never execute tool calls with side effects (writes, sends, deletes) in parallel with reads that inform them. If Tool B depends on Tool A's result, enforce sequential execution at the orchestration layer.

### 1.4 Streaming with Tool Calls

**Anthropic**: The stream emits typed server-sent events. Text deltas, `input_json_delta` fragments for tool call parameters, and thinking blocks arrive interleaved on separate content block indices. Reconstruct by grouping on index, not arrival order. Fine-Grained Tool Streaming (GA February 5, 2026) removes buffering and streams tool parameters immediately as they are generated, rather than waiting for complete valid JSON. ([Anthropic streaming docs](https://platform.claude.com/docs/en/build-with-claude/streaming), [Fine-grained tool streaming](https://platform.claude.com/docs/en/agents-and-tools/tool-use/fine-grained-tool-streaming))

**OpenAI (Responses API)**: Key streaming events: `response.output_item.added` (emitted when model begins a function call, includes name and call_id), `response.function_call_arguments.delta` (incremental JSON chunks), `response.function_call_arguments.done` (complete arguments). Accumulate deltas by `output_index`.

**Cross-provider proxy warning**: Proxying Claude through OpenAI-compatible gateways (LiteLLM, llm-gateway) during streaming can produce invalid tool-use events, empty `tool_use` blocks, or truncated JSON. Use native SDKs where possible.

### 1.5 Dynamic Tool Discovery

**MCP Protocol (2026-07-28 spec)**: Clients discover tools via `tools/list`, execute via `tools/call`. The 2026-07-28 spec made MCP stateless (request/response), removing protocol-level sessions. Tool and method names travel in `Mcp-Method` and `Mcp-Name` HTTP headers for gateway routing. Dynamic Client Registration (DCR) is deprecated in favor of Client ID Metadata Documents (CIMD). Progressive discovery is planned so servers can expose a small entry point and reveal more of their catalog as the conversation narrows. MCP Server Cards (`.well-known` URLs) are under development for discovery without connecting. ([MCP 2026-07-28 spec](https://modelcontextprotocol.io/specification/2026-07-28), [MCP blog](https://blog.modelcontextprotocol.io/posts/2026-07-28/))

**Anthropic Tool Search Tool**: Enables agents to work with hundreds/thousands of tools by dynamically discovering and loading on demand. Tools with `defer_loading: true` are stripped from the rendered tools section, preserving prompt cache. When tool search discovers a deferred tool, it returns `tool_reference` blocks (up to 5 per search by default), which the API expands into full definitions inline. References persist across turns -- no re-searching needed. Benchmarks: traditional approach consumes ~77K tokens for 50+ MCP tools; with tool search it drops to ~8.7K tokens. ([Anthropic tool search docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool), [Anthropic advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use))

**OpenAI `allowed_tools`**: The `tool_choice` parameter supports `type: "allowed_tools"` to restrict callable tools to a subset per turn, enabling prompt caching while keeping the full tool list stable.

**Claude mid-conversation tool changes (beta, 2026-07-01)**: Claude Opus 5 supports adding and removing tools mid-conversation through `tool_addition` and `tool_removal` content blocks on `role: "system"` messages, instead of re-sending the full top-level tools array.

### 1.6 Pydantic Integration

Pydantic serves as the schema definition layer between Python code and LLM APIs. `BaseModel.model_json_schema()` generates JSON Schema compatible with both Anthropic and OpenAI tool definitions. `model_validate_json()` validates raw LLM output against the schema at runtime. ([Pydantic JSON Schema docs](https://pydantic.dev/docs/validation/dev/concepts/json_schema/), [Pydantic LLM intro](https://pydantic.dev/articles/llm-intro))

**Workflow**: Define a Pydantic model -> use `.model_json_schema()` as the `parameters`/`input_schema` value in the tool definition -> validate model response with `model_validate_json()` -> on `ValidationError`, feed the error message back to the LLM for self-correction (cap retries at 3).

**Framework integration**: Instructor (structured LLM output via Pydantic), LangChain (output parsers), PydanticAI (native integration with tool schemas), OpenAI SDK (native `response_format` with Pydantic), Anthropic SDK (via `input_schema`).

**Production pattern**: Define `BaseModel` schemas for inputs, tool calls, and outputs before prompts or routing. Validate at every boundary. Feed `ValidationError` messages back to the model with retry limits. Log prompt, model ID, schema_version, and validated payload. Add `schema_version` field for migration.

### 1.7 Tool Result Formatting

Return semantic, stable identifiers (slugs, UUIDs) rather than opaque internal references. Include only fields the model needs for its next reasoning step. For no-return functions (e.g., `send_email`), return a success/failure indicator string. OpenAI supports image/file results as an array of objects in `function_call_output`. Large results should be truncated or summarized before injection -- bloated responses waste context and degrade reasoning quality.

### 1.8 `tool_choice` Parameter Comparison

| Option | Anthropic | OpenAI |
|--------|-----------|--------|
| Model decides | `{"type": "auto"}` (default) | `"auto"` (default) |
| Must use a tool | `{"type": "any"}` | `"required"` |
| Specific tool | `{"type": "tool", "name": "X"}` | `{"type": "function", "name": "X"}` |
| No tools | `{"type": "none"}` | `"none"` |
| Subset restriction | N/A (use tool search + defer_loading) | `{"type": "allowed_tools", "tools": [...]}` |

**Anthropic caveat**: Claude Opus 5.5, Fable 5.1, and Mythos 5.1 do not support forced tool use (`any` or `tool` return 400 errors). Use `auto` with `strict: true` instead.

---

## 2. Token Economics & NFR Metrics

### 2.1 Token Cost of Tool Definitions

Tool schemas are serialized into the model's input context on every call, counted as input tokens. There is no concept of "free metadata."

| Tool Complexity | Tokens per Tool |
|----------------|-----------------|
| Minimal (1 param, brief description) | ~15 tokens |
| Realistic production (multiple params, detailed description) | 40-80 tokens |
| Complex (nested objects, enums, multiple examples) | 100-200+ tokens |

**Provider differences**: Claude tokenizes at ~0.8x the rate of GPT-4 (cl100k_base), so expect ~30-65 tokens per tool for Claude vs 40-80 for OpenAI models. ([Token overhead analysis](https://theneuralbase.com/function-calling-theory/learn/intermediate/token-overhead/))

**Scaling**: 20 tools x 40-80 tokens = 800-1,600 tokens overhead per request. Anthropic's own analysis: a setup with GitHub, Slack, Sentry, and Grafana MCP servers can consume ~55,000 tokens in tool definitions before the model processes a single user instruction. Systems with 50+ tools often hit 50-60% overhead and become economically unsustainable without filtering. ([Scalekit token efficiency](https://www.scalekit.com/blog/token-efficient-tool-calling), [Neural Base context consumption](https://theneuralbase.com/function-calling-theory/learn/intermediate/context-window-consumption/))

**Input examples**: ~20-50 tokens for simple examples, ~100-200 tokens for complex nested objects. First example improves accuracy from ~80% to ~92%; fifth example barely moves the needle. Production systems often use zero examples in schemas. ([Anthropic define-tools docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools))

### 2.2 Cost Optimization Strategies

1. **Dynamic tool gating**: Filter from 20 tools to 1-2 relevant tools per request. Saves ~760 tokens/request; at 1,000 requests/day = ~760K tokens saved (~$0.76/day, model-dependent). 40-50% reduction in token usage.
2. **Tool Search / defer_loading**: Traditional 50+ MCP tools = ~77K tokens; with tool search = ~8.7K tokens (~89% reduction).
3. **Prompt caching**: Put stable content (system instructions, tool definitions) first, variable content last. Tool definitions are prime caching candidates. Changing `tool_choice` invalidates cached message blocks (but tool definitions and system prompts remain cached).
4. **Remove credential parameters from schemas**: Two credential params (oauth_token, client_id) add ~80-120 tokens per tool; across 5 tools = 400-600 tokens wasted. Resolve credentials at execution time via vault injection.
5. **Cache-friendly ordering**: Avoid cache busters: timestamps in system prompts, shuffled few-shot examples, dynamic tool lists.

### 2.3 Latency Overhead

Expect 3-5x higher token usage for function-calling workflows vs text-only baselines for the same task. Each tool calling round trip adds: model inference latency + tool execution time + network overhead. Multi-step chains (3-5 tool calls) can push total latency to 5-15 seconds for complex tasks. Parallel tool calls reduce latency by up to 3.7x (LLMCompiler, ICML 2024). ([MindStudio context costs](https://www.mindstudio.ai/blog/advanced-context-engineering-token-savings))

### 2.4 Benchmark Results: BFCL

**Berkeley Function Calling Leaderboard (BFCL)**:

BFCL v4 (April 2026) evaluation structure: Agentic (40%), Multi-Turn (30%), Live (10%), Non-Live (10%), Hallucination (10%).

BFCL v3 top scores (June 2026): GLM 4.5 at 76.7%, Claude Opus 4.7 at 76.6%, Gemini 3.1 Flash Lite Preview at 76.5%. Frontier closed APIs vs top open-weight models gap: 3-4 percentage points. ([BFCL v4 leaderboard](https://gorilla.cs.berkeley.edu/leaderboard.html), [BFCL v3 scores](https://pricepertoken.com/leaderboards/benchmark/bfcl-v3))

**Quality concern**: Epoch AI audit found defects in 48% of a random sample of 50 tasks (from 5,088 scored items), raising questions about benchmark reliability. ([Epoch AI review](https://epoch.ai/benchmarks/berkeley-function-calling-leaderboard/review))

**Key finding**: Models excel at single-turn calls; memory, dynamic decision-making, and long-horizon reasoning remain open challenges.

### 2.5 Rate Limit Implications

LLM API calls fail 1-5% of the time due to rate limits, timeouts, and server errors. Analysis of LLM API traffic (February 2026): 5% of all LLM call spans reported errors; 60% of those caused by rate limits. Multi-tool workflows amplify this: a 5-step tool chain with 5% per-step failure rate = ~23% chance of at least one failure per workflow. ([Openlayer failure modes](https://www.openlayer.com/blog/ai-agent-failure-modes-tool-calling-loops-propagation))

---

## 3. Distributed Resilience & State

### 3.1 Retry Logic for Failed Tool Executions

**Error classification matrix** (prerequisite for correct retry logic):

| Error Type | Owner | Strategy |
|-----------|-------|----------|
| Transient (timeout, rate limit, 503) | Infrastructure | Exponential backoff with jitter, max 3-5 retries |
| LLM-recoverable (malformed JSON, wrong params) | Model | Self-correction: return structured error to model, let it retry with corrected inputs |
| User-fixable (missing permissions, invalid input) | User | Surface to user via `interrupt()` or equivalent |
| Unexpected (crashes, unhandled exceptions) | Developer | Crash loudly, alert, do not retry |

**Self-correction over blind retries**: If schema validation fails, do not retry with the same prompt. Return the validation error to the model as a tool_result. This reduces failure rates by ~40% compared to naive retries. ([Agent error recovery patterns](https://devops.gheware.com/blog/posts/agent-tool-calling-reliability-failure-2026.html))

**Exponential backoff with jitter** (for transient errors only): AWS research shows this reduces retry storms by 60-80%. Implement in the tool execution wrapper, not in the LLM reasoning loop (LLM reasoning is expensive and often picks the wrong retry strategy). ([AI agent retry patterns](https://fast.io/resources/ai-agent-retry-patterns/))

### 3.2 Handling Tool Timeouts

Implement deadlines at the tool execution layer, not the model layer. Return a structured timeout message as the tool_result: `"Tool execution timed out after 30s. Consider using a more specific query or breaking the request into smaller parts."` Include recovery hints -- bare error strings cause the model to retry identically.

### 3.3 Idempotency in Tool Execution

Critical for any tool with side effects. If an agent retries a step, can it safely execute twice? Sending emails, charging cards, inserting rows -- all real failure modes in production.

**Solutions**: Idempotency keys (unique per tool call ID), transactional locks for stateful tools, deduplication at the execution layer. Multi-step workflows that fail at step 4 and retry from step 1 will re-execute steps 1-3, causing duplicate side effects. Use checkpoint/resume patterns: persist completed step results, resume from the last successful step. ([Error recovery patterns](https://skills.mercuryagent.sh/skills/ai-ml/error-recovery-retry))

### 3.4 State Management Across Multi-Step Tool Chains

Multi-step AI workflows require explicit state management. Options:

- **Conversation history as state**: Append all tool calls and results to the message array. Simple but grows context linearly.
- **External state store**: Persist intermediate results to a database/cache, pass only references in context. More complex but scalable.
- **LangGraph/workflow engines**: Graph-based state machines with typed state, reducers, and checkpointing. ([LangGraph error handling](https://focused.io/lab/langgraph-agent-error-handling-production))

### 3.5 Circuit Breaker Patterns

If a tool fails 3 times in 5 minutes, open the circuit. Stop retrying. Do not rely on the model to decide when to stop -- enforce at the orchestration layer. Three states: Closed (normal operation), Open (all calls fail-fast), Half-Open (allow one test call to check recovery).

**Layered architecture**: Provider client layer handles backoff, Retry-After headers, circuit breaker state (knows nothing about business logic). Orchestration layer handles fallback chains, budget guardrails, saga state management. ([AI error handling patterns 2026](https://valuestreamai.com/blog/ai-error-handling-patterns-2026))

### 3.6 Tool Execution Sandboxing

Run untrusted tool code in isolated environments: Docker containers, WASM runtimes, subprocess with resource limits (CPU, memory, time). Anthropic's server-side code_execution tool runs in a sandboxed environment on Anthropic infrastructure. For client-side tools, the execution boundary is the key safety property -- Claude emits a request, your code decides whether and how to run it.

### 3.7 Fallback Chains

When a primary tool or model is unavailable: define fallback sequences at both the model layer (swap to secondary model/provider) and the tool layer (use cached/approximate results, degrade gracefully). Monitor fallback activation rates as a reliability signal.

---

## 4. Enterprise Security & Governance

### 4.1 Tool-Level RBAC

Apply Role-Based Access Control at the tool invocation layer. A guest session cannot invoke admin tools; an unauthenticated session cannot access sensitive data. Define roles based on job functions (Data Scientist, ML Engineer, Security Auditor) with specific tool permissions per role.

**Anthropic `allowed_callers`**: Restricts which callers can invoke a tool (e.g., `"allowed_callers": ["code_execution_20260120"]` guides Claude to call the tool only from code execution). The response's `tool_use` block includes a `caller` field. ([Anthropic tool reference](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-reference))

**MCP Gateway RBAC**: MintMCP provides SSO, SCIM-driven RBAC, IdP groups, tool-level allowlisting, and rule-based policy. TrueFoundry provides namespace-based multi-tenant isolation with per-team credentials and quotas. ([MintMCP gateway](https://www.mintmcp.com/blog/gateways-enterprise-engineering-with-mcp), [TrueFoundry gateway](https://www.truefoundry.com/blog/agent-gateway))

### 4.2 Input Validation and Sanitization

Treat every tool call as untrusted input. Validate before execution:
- Schema validation (JSON Schema `strict: true` on both Anthropic and OpenAI)
- Type checking (model sometimes returns `"2"` instead of `2`)
- Enum constraint enforcement (prevents hallucinated parameter values)
- Input normalization (strip zero-width characters, normalize whitespace)
- Range/length validation for numeric and string parameters

### 4.3 Output Validation

Tool results can contain injection payloads (indirect prompt injection). Defenses:
- Content segregation: never concatenate raw tool output directly into system prompt without structural delimiter
- Dual-LLM patterns: use a separate validator model to check tool results before injecting into the main model's context
- Output scanning: LLM Guard (Protect AI), Lakera Guard, Azure AI Content Safety for runtime inspection
- Typed tool calls via MCP reduce injection surface by structuring data flow

OWASP LLM Top 10 ranks prompt injection as LLM01 (2025 release, still top concern in 2026). May 2026 survey: prompt injection present in 73% of production AI deployments during 2025. ([OWASP LLM Prompt Injection](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html))

### 4.4 Audit Logging

Every tool invocation should be a discrete observability span tracking:
- Tool name, parameters, caller identity, tenant ID
- Execution duration (P50, P95, P99)
- Success/failure status and error type
- Unique trace IDs across the full agent workflow (OpenTelemetry / LangSmith)
- Tool call success rate broken down by error type (validation, network, semantic)

MCP gateways (Bifrost, MintMCP, TrueFoundry, Lunar.dev MCPX) provide centralized audit logging with SOC 2 Type II and HIPAA alignment. ([Integrate.io MCP gateways](https://www.integrate.io/blog/best-mcp-gateways-and-ai-agent-security-tools/))

### 4.5 Sandboxed Execution Environments

- **Docker**: Per-tool container isolation, resource limits, network policies
- **WASM**: Lightweight isolation, portable, fast cold-start
- **Subprocess with cgroups**: Linux-level resource isolation
- **Anthropic server tools**: web_search, web_fetch, code_execution run on Anthropic infrastructure in sandboxed environments
- **Supabase RLS**: Row-Level Security for tenant isolation in database-backed tools (used by beme08/agent-gateway for multi-tenant HR policy agent)

### 4.6 Rate Limiting Per Tool

Implement at the gateway layer. Different tools have different risk profiles:
- Read-only tools: higher limits (e.g., 100 RPM)
- Write tools: lower limits (e.g., 10 RPM)
- Destructive tools (delete, transfer): very low limits (e.g., 2 RPM) with human approval gates
- Cost attribution per tenant/team enables chargeback and abuse detection

---

## 5. Production Failure Modes

### 5.1 Hallucinated Tool Names or Parameters

The model fabricates tool names, produces malformed arguments, or calls tools with plausible but nonexistent parameter values (wrong IDs, made-up API paths). Root cause: the model generates tool calls via pattern matching, not database lookups.

**Prevalence**: Tool calling fails 3-15% of the time in production depending on model size and task complexity. Some agent workflows fail ~41% of the time. ([Openlayer failure modes](https://www.openlayer.com/blog/ai-agent-failure-modes-tool-calling-loops-propagation))

**Mitigations**: Schema validation before execution (zero validation steps between model output and side effect = guaranteed problems). Tool registry enforcement with explicit parameter schemas. Strongly typed schemas with enum constraints. Use `strict: true` on both providers.

### 5.2 Schema Validation Failures and Recovery

Model returns incompatible types (`"2"` instead of `2`), omits required fields, or adds unexpected fields. Without `strict: true`, these are best-effort and may silently break downstream code.

**Recovery pattern**: Catch `ValidationError`, format it as a structured tool_result message with the specific error and expected format, send back to the model. Cap self-correction retries at 3. Anthropic's `strict: true` and OpenAI's `strict: true` prevent most type-level issues.

### 5.3 Infinite Tool Calling Loops

**IAL-Scan** (academic study) examined 6,549 LLM agent repositories and found 68 confirmed infinite agentic loop failures across 47 projects, with 91.9% precision. This is a systemic design pattern being shipped to production regularly. ([IAL-Scan paper](https://arxiv.org/html/2607.01641v1))

**Causes**: Ambiguous tool responses ("Found 2 flights, more may be available" -- no terminal signal), vague goal definitions ("help the user"), model retrying the same failed tool call.

**Prevention**:
- Explicit iteration caps (hard limit of 10-25 tool calls per conversation)
- Per-tool debounce: if same tool + same parameters appears 3 times, block the third attempt
- Clear terminal states in tool responses: "SUCCESS: Booking HT79265 confirmed" (in one demo, reduced tool calls from 14 to 2, a 7x improvement)
- System prompt must define unambiguous "task complete" criteria
- Budget guardrails: max token spend per conversation

### 5.4 Tool Result Too Large for Context Window

Large API responses, database query results, or file contents can consume the remaining context window, pushing out earlier conversation turns or causing truncation.

**Mitigations**: Truncation at the tool execution layer (return first N characters with "...truncated"). Summarization via a smaller/faster model before injection. Return only relevant fields (projection). Pagination with explicit "more available" signals.

### 5.5 Type Coercion Errors

Model returns `"42"` (string) instead of `42` (integer), or `"true"` instead of `true`. Without strict mode, these propagate silently.

**Solution**: Always enable strict mode (`strict: true`). For non-strict scenarios, add a coercion layer in the tool execution wrapper (parse strings to expected types with explicit error handling).

### 5.6 Cascading Failures in Tool Chains

A hallucinated output in step 2 becomes a malformed input in step 3, and by step 5 the agent operates on a premise that was never true. Cascading failure is especially dangerous for tools with side effects -- a hallucinated parameter can create records, charge payments, or trigger irreversible processes.

**Mitigations**: Validate outputs at each step before passing forward. Define fallback behavior for each validation check. Implement saga pattern (compensating transactions) for multi-step workflows with side effects.

Five agents at 95% individual accuracy deliver ~77% overall success rate. Multi-agent systems show failure rates between 41% and 86.7% in production. ([Multi-agent failure modes](https://niteagent.com/blog/multi-agent-failure-modes-7-patterns-that-break-production-systems/))

### 5.7 Model Refusing to Use Available Tools

The model answers from its training data instead of calling a relevant tool. Causes: tool description doesn't clearly indicate when to use it, model's prior training biases, too many tools creating selection ambiguity.

**Mitigations**: Use `tool_choice: "required"` / `"any"` when a tool call is mandatory. Write detailed tool descriptions (aim for 3-4+ sentences). Consolidate related tools to reduce ambiguity. Add explicit instructions in the user message: "Use the get_weather tool in your response."

### 5.8 Context Degradation (Silent Failure)

Over long sessions, the agent's internal representation of the original task compresses, earlier constraints get deprioritized, and the agent reasons against a progressively incomplete picture of its goal. No exception fires. The system reports healthy. This is not a crash -- it is a slow drift into incorrect behavior.

---

## 6. Enterprise System Design Scenarios

### 6.1 Enterprise Tool Gateway with RBAC and Audit Logging

**Architecture** (based on production patterns from MintMCP, TrueFoundry, Bifrost):

```
[Client/Agent] --> [API Gateway (auth, rate limit)]
                      |
                      v
               [Tool Gateway Layer]
               ├── Authentication (JWT, SSO via SAML/OIDC)
               ├── Authorization (RBAC with SCIM-driven roles)
               ├── Tool Registry (MCP tools/list, schema validation)
               ├── Rate Limiting (per-tool, per-tenant)
               ├── Input Validation (schema + sanitization)
               ├── Audit Logger (every invocation as discrete span)
               ├── Circuit Breaker (per-tool failure tracking)
               └── Cost Attribution (per-tenant token/call metering)
                      |
                      v
               [Tool Execution Layer]
               ├── Sandboxed Environments (Docker/WASM)
               ├── Credential Injection (vault-backed, not in schema)
               ├── Output Validation (injection scanning)
               └── Result Formatting (truncation, projection)
                      |
                      v
               [Observability Layer]
               ├── OpenTelemetry Traces (unique trace IDs)
               ├── Per-tool P50/P95/P99 latency
               ├── Error rate by type (validation, network, semantic)
               └── Alerting (circuit breaker opens, budget exceeded)
```

**Key design decisions**:
- Method and tool names in HTTP headers (`Mcp-Method`, `Mcp-Name`) for gateway routing/authorization without body parsing (MCP 2026-07-28 spec)
- Namespace-based multi-tenant isolation: each team/project gets its own virtual gateway instance with separate credentials and quotas
- Tool definitions stored centrally; clients receive only authorized subset
- Hybrid architecture: server-side orchestration enforces security-critical invariants (authorization, state isolation, audit logging); client-side frameworks control agent composition and latency-sensitive operations

**Reference implementations**:
- [MCP Enterprise Tool Gateway](https://glama.ai/mcp/servers/BhavikMoradiya/mcp-enterprise-tool-gateway) -- production-grade MCP server with JWT auth, RBAC, rate limiting, audit logging
- [beme08/agent-gateway](https://github.com/beme08/agent-gateway) -- multi-tenant agentic AI platform with ACL-filtered RAG and role-gated tools
- [Bifrost](https://www.integrate.io/blog/best-mcp-gateways-and-ai-agent-security-tools/) -- open-source, runs LLM gateway + MCP gateway as single binary with unified virtual-key system, audit log, and observability pipeline

### 6.2 Dynamic Tool Registry for Multi-Tenant Agent Platform

**Architecture**:

```
[Tenant Admin UI] --> [Tool Registry Service]
                         ├── Tool CRUD (register, update, deprecate, retire)
                         ├── Schema Validation (JSON Schema draft 2020-12)
                         ├── Version Management (schema_version field)
                         ├── Tenant Scoping (tool visibility per tenant)
                         ├── Category/Tag System (for tool search)
                         └── Health Check (per-tool availability monitoring)
                              |
                              v
                      [Discovery Layer]
                         ├── MCP tools/list (filtered by tenant auth)
                         ├── Tool Search (BM25 + regex over names/descriptions)
                         ├── Progressive Disclosure (small entry point,
                         |   reveal more as conversation narrows)
                         └── Capability Negotiation (server/discover)
                              |
                              v
                      [Runtime Layer]
                         ├── Dynamic Loading (defer_loading: true)
                         ├── Hot Reload (add/remove tools mid-conversation)
                         ├── Prompt Cache Management (stable prefix for
                         |   cached tools, variable suffix for dynamic)
                         └── Token Budget Enforcement (max tools per request)
```

**Key patterns**:
- Keep 3-5 most-used tools always loaded (non-deferred); defer the rest
- Tool selection accuracy degrades with 30+ tools loaded at once; use tool search for larger registries
- Traditional RBAC is insufficient for multi-tenant -- a user might be an "Editor" everywhere. Implement tenant-scoped RBAC with virtual walls at identity, data, and resource layers
- Context-aware filtering (2-5 relevant tools per request) reduces overhead to 5-10% of token budget
- Use `allowed_callers` to restrict dangerous tools to specific execution contexts

**Observability requirements**: Each tool invocation is a discrete span. Track per-tool P50, P95, P99 latencies. Break down success rate by error type. A tool with 15% validation error rate has a schema problem; a tool with 5% network error rate needs better retry logic.

---

## Sources

- [1] [Anthropic - Define tools](https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools) -- Tool definition schema, input_examples, best practices
- [2] [Anthropic - Tool use overview](https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview) -- Client vs server tools, dispatch lifecycle
- [3] [Anthropic - Handle tool calls](https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls) -- tool_use/tool_result block formats
- [4] [Anthropic - Implement tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/implement-tool-use) -- Agentic loop implementation
- [5] [Anthropic - Strict tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use) -- Schema validation guarantees
- [6] [Anthropic - Fine-grained tool streaming](https://platform.claude.com/docs/en/agents-and-tools/tool-use/fine-grained-tool-streaming) -- GA Feb 2026, unbuffered parameter streaming
- [7] [Anthropic - Streaming messages](https://platform.claude.com/docs/en/build-with-claude/streaming) -- SSE event types for tool use
- [8] [Anthropic - Tool search tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool) -- Dynamic tool discovery, defer_loading
- [9] [Anthropic - Advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use) -- Tool search, programmatic tool calling
- [10] [Anthropic - Tool reference](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-reference) -- allowed_callers, defer_loading, cache_control
- [11] [OpenAI - Function calling guide](https://developers.openai.com/api/docs/guides/function-calling) -- Tool definition, strict mode, parallel calls, streaming
- [12] [BFCL v4 Leaderboard](https://gorilla.cs.berkeley.edu/leaderboard.html) -- Berkeley Function Calling benchmark
- [13] [BFCL v3 Scores](https://pricepertoken.com/leaderboards/benchmark/bfcl-v3) -- Model comparison scores (June 2026)
- [14] [Epoch AI - BFCL Review](https://epoch.ai/benchmarks/berkeley-function-calling-leaderboard/review) -- Quality audit finding 48% defect rate in sample
- [15] [MCP 2026-07-28 Specification](https://modelcontextprotocol.io/specification/2026-07-28) -- Stateless protocol, HTTP header routing
- [16] [MCP Blog - 2026-07-28 Release](https://blog.modelcontextprotocol.io/posts/2026-07-28/) -- CIMD, deprecation of DCR
- [17] [WorkOS - MCP in 2026](https://workos.com/blog/everything-your-team-needs-to-know-about-mcp-in-2026) -- Enterprise MCP adoption
- [18] [Pydantic - JSON Schema docs](https://pydantic.dev/docs/validation/dev/concepts/json_schema/) -- model_json_schema(), mode parameter
- [19] [Pydantic - LLM intro](https://pydantic.dev/articles/llm-intro) -- Validation for LLM outputs
- [20] [Scalekit - Token-efficient tool calling](https://www.scalekit.com/blog/token-efficient-tool-calling) -- Auth overhead, credential removal from schemas
- [21] [Neural Base - Token overhead](https://theneuralbase.com/function-calling-theory/learn/intermediate/token-overhead/) -- Quantitative token cost per tool
- [22] [Neural Base - Context window consumption](https://theneuralbase.com/function-calling-theory/learn/intermediate/context-window-consumption/) -- Budget allocation analysis
- [23] [MindStudio - Context cost cutting](https://www.mindstudio.ai/blog/advanced-context-engineering-token-savings) -- Tool overhead optimization
- [24] [Gheware DevOps - Agent reliability](https://devops.gheware.com/blog/posts/agent-tool-calling-reliability-failure-2026.html) -- Error classification matrix
- [25] [Zylos Research - Production architecture patterns](https://zylos.ai/research/2026-04-16-tool-augmented-llm-agents-production-architecture/) -- Layered error handling
- [26] [n8n Blog - LLM error handling](https://blog.n8n.io/llm-tool-calling-error-handling/) -- Retries and fallbacks
- [27] [Fastio - AI agent retry patterns](https://fast.io/resources/ai-agent-retry-patterns/) -- Exponential backoff with jitter
- [28] [ValueStreamAI - AI error handling patterns](https://valuestreamai.com/blog/ai-error-handling-patterns-2026) -- Circuit breakers, layered architecture
- [29] [Mercury Skills - Error recovery](https://skills.mercuryagent.sh/skills/ai-ml/error-recovery-retry) -- Idempotency patterns
- [30] [OWASP - LLM Prompt Injection Prevention](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html) -- Input/output validation, content segregation
- [31] [Openlayer - Agent failure modes](https://www.openlayer.com/blog/ai-agent-failure-modes-tool-calling-loops-propagation) -- Tool calling errors, infinite loops, propagation
- [32] [IAL-Scan paper](https://arxiv.org/html/2607.01641v1) -- Infinite agentic loop analysis across 6,549 repositories
- [33] [Niteagent - Multi-agent failure modes](https://niteagent.com/blog/multi-agent-failure-modes-7-patterns-that-break-production-systems/) -- Cascading failures, reliability math
- [34] [MCP Enterprise Tool Gateway](https://glama.ai/mcp/servers/BhavikMoradiya/mcp-enterprise-tool-gateway) -- RBAC, audit, rate limiting reference implementation
- [35] [MintMCP Gateway](https://www.mintmcp.com/blog/gateways-enterprise-engineering-with-mcp) -- Governance-first architecture
- [36] [TrueFoundry Agent Gateway](https://www.truefoundry.com/blog/agent-gateway) -- Multi-tenant gateway design
- [37] [Integrate.io - MCP gateways](https://www.integrate.io/blog/best-mcp-gateways-and-ai-agent-security-tools/) -- Gateway comparison, Bifrost
- [38] [Agent gateway (GitHub)](https://github.com/beme08/agent-gateway) -- Multi-tenant platform with ACL-filtered RAG
