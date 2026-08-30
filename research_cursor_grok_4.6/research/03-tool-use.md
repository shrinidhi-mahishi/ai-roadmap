# Research: Tool Use

**Date researched**: 2026-08-21
**Sources consulted**: 80

Scope: APIs (OpenAPI/JSON Schema adapters, REST/gRPC/GraphQL, auth, idempotency, pagination, retries, webhook vs poll); native function calling (OpenAI / Anthropic / Gemini, parallel, `tool_choice`, strict schemas, tool-result injection, hallucinated params); browser (browser-use, Playwright, Computer Use); code execution (gVisor, Firecracker, WASM, E2B, OpenAI Code Interpreter; egress; resource limits). Prices and SLOs below are from vendor docs or named benches as of 2026-08-21. ⚠️ Vendor dashboards are the source of truth for org-specific RPM/TPM. No unpublished latency SLOs are invented; missing percentiles are marked ⚠️.

---

## 1. System Topology & Mechanics

### 1.1 Control plane vs data plane

Tool use is a **two-plane** system. The **control plane** is the model API plus the agent runtime: schema compilation, `tool_choice` / `FunctionCallingConfig`, parallel-call packing, stop-reason parsing, loop budget, and (for hosted tools) the provider’s internal dispatcher. The **data plane** is everything that actually mutates the world: your REST/gRPC/GraphQL adapters, MCP servers, Playwright/CDP sessions, Firecracker/gVisor/WASM sandboxes, and webhook receivers.

Invariant across OpenAI, Anthropic, Gemini, and Bedrock: **the model does not execute client tools**. It emits a structured call (`function_call` / `tool_use` / `toolUse`); the application or a designated server-side runtime executes; a result is injected back under a correlating ID ([OpenAI function calling](https://developers.openai.com/api/docs/guides/function-calling); [Anthropic tool-use overview](https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview); [Gemini tools](https://ai.google.dev/gemini-api/docs/tools); [Bedrock tool use](https://docs.aws.amazon.com/bedrock/latest/userguide/tool-use.html)).

**Hosted / server tools** invert the data plane: OpenAI Responses built-ins (web search, file search, code interpreter, hosted shell, hosted MCP), Anthropic `web_search` / `web_fetch` / `code_execution` / `tool_search`, Gemini built-in Search and Code Execution, and Bedrock **server-side** tool mode (Lambda or AgentCore Gateway on Responses) execute inside the provider. The control plane still owns the conversation; the data plane is the provider’s sandbox/search index ([OpenAI function calling — built-in tools](https://developers.openai.com/api/docs/guides/function-calling); [Anthropic overview — client vs server](https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview); [Bedrock three modes](https://docs.aws.amazon.com/bedrock/latest/userguide/tool-use.html)).

**Mixed parallel groups** are a first-class topology hazard. Anthropic: if Claude mixes a server tool with a client tool in one parallel batch, you may get `stop_reason: "tool_use"` and must complete the client results before the server path continues ([Anthropic overview](https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview); [stop reasons](https://platform.claude.com/docs/en/build-with-claude/handling-stop-reasons#tool-use)). OpenAI: on GPT-5+ , custom functions may run in parallel, but **built-in tools cannot share a parallel function-call batch** ([OpenAI function calling](https://developers.openai.com/api/docs/guides/function-calling)). Gemini 3: built-in + custom in one turn uses **tool context circulation** via `previous_interaction_id` ([Gemini tools](https://ai.google.dev/gemini-api/docs/tools)).

### 1.2 Agent-to-tool protocols and the dispatcher

Four protocol layers appear in production:

| Layer | Contract | Who executes | Typical transport |
| --- | --- | --- | --- |
| Native function calling | JSON Schema / OpenAPI-subset parameters | App or provider | HTTPS to model API; app-local dispatch |
| MCP | JSON-RPC tools/list + tools/call; HTTP+OAuth or STDIO | MCP server | Streamable HTTP, STDIO, hosted MCP |
| Framework tools | LangGraph `ToolNode`, Agents SDK `FunctionTool`, ADK `FunctionTool`, CrewAI `BaseTool` | Worker process | In-process or activity |
| Computer / browser / shell | Dated toolsets / CDP / `computer_call` | Isolated VM/browser | Screenshots, a11y tree, or PTY |

**Dispatcher contract (sync):** parse `tool_calls[]` / `tool_use` blocks → validate against JSON Schema (strict/validated path) → authorize (RBAC + allowlist) → execute with timeout/idempotency key → map errors to `tool_result` / `is_error: true` / function response → re-inject **all** IDs in one user/tool turn.

**Dispatcher contract (async / streaming):** OpenAI Responses streams `response.function_call_arguments.delta`; Gemini Interactions streams `step.delta` partial `arguments` (aggregate before execute); Gemini Enterprise `stream_function_call_arguments` / `streamFunctionCallArguments` ([Gemini function calling](https://ai.google.dev/gemini-api/docs/function-calling); [Gemini Enterprise FC](https://docs.cloud.google.com/gemini-enterprise-agent-platform/reference/models/function-calling)). **Do not execute on partial JSON.** Anthropic programmatic tool calling (`code_execution_20260120`+) pauses the server container and emits client `tool_use` with `caller` pointing at the code-execution run; continuation must echo the same `container` and tool list ([programmatic tool calling](https://platform.claude.com/docs/en/agents-and-tools/tool-use/programmatic-tool-calling)).

**MCP** is the cross-runtime adapter: `tools/list` → convert to OpenAI `{type:"function"}`, Anthropic `input_schema`, Gemini `functionDeclarations` (strip `$schema` / `additionalProperties` / `$defs` — Gemini rejects JSON-Schema-only keys) ([MCP authorization 2025-11-25](https://modelcontextprotocol.org/specification/2025-11-25/basic/authorization); [OpenAI Agents SDK MCP](https://openai.github.io/openai-agents-python/mcp/)). Hosted MCP on Responses: the model lists and invokes remote tools without a callback into your Python process ([Agents SDK MCP](https://openai.github.io/openai-agents-python/mcp/)).

**Framework dispatchers:**
- **LangGraph**: `model.bind_tools` + `ToolNode` + `tools_condition` is the ReAct loop as a graph; checkpointers (Memory/Sqlite/Postgres) persist messages including tool I/O ([LangGraph tools pattern](https://www.crewship.dev/learn/langgraph-tools)).
- **OpenAI Agents SDK**: `FunctionTool` | `HostedMCPTool` | `CodeInterpreterTool` | `ComputerTool` | `ShellTool` | `ToolSearchTool` | `ProgrammaticToolCallingTool`; `activity_as_tool` when wrapped by Temporal ([Agents SDK tools](https://openai.github.io/openai-agents-js/guides/tools/); [Temporal contrib](https://github.com/temporalio/sdk-python/blob/53ae9fc7/temporalio/contrib/openai_agents/README.md)).
- **Google ADK**: native functions auto-wrap as `FunctionTool`; `LongRunningFunctionTool` pauses the runner for HITL / async ops; `ToolContext.actions` can `skip_summarization` or `transfer_to_agent` ([ADK function tools](https://google.github.io/adk-docs/tools-custom/function-tools/); [ADK custom tools](https://google.github.io/adk-docs/tools-custom/)).
- **CrewAI**: `@tool` / `BaseTool`; optional crew-level `function_calling_llm`; async `_arun`; result `cache_function` ([CrewAI tools](https://docs.crewai.com/en/learn/create-custom-tools); [Crews](https://docs.crewai.com/edge/en/concepts/crews)).
- **Bedrock Converse**: `toolConfig.tools[].toolSpec.inputSchema.json` + `toolChoice` `{auto|{tool:{name}}}` ; `stopReason: tool_use`; echo `toolUseId` on `toolResult` ([Converse API](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html); [Bedrock recipes](https://aws-samples.github.io/amazon-bedrock-samples/agents-and-function-calling/function-calling/function_calling_with_converse/function_calling_with_converse/)).

### 1.3 APIs as tools: OpenAPI / JSON Schema, REST / gRPC / GraphQL adapters

**Schema language.** Native tools are JSON Schema objects (`type: object`, `properties`, `required`). OpenAI **strict** mode additionally requires `additionalProperties: false` on every object and **all** `properties` keys in `required` (optional fields via `type: ["string","null"]`) ([OpenAI function calling](https://developers.openai.com/api/docs/guides/function-calling)). Anthropic uses `input_schema` (JSON Schema). Gemini uses an **OpenAPI subset** under `functionDeclarations[].parameters`. MCP `inputSchema` is JSON Schema 2020-12 in current servers. Production adapters therefore: OpenAPI 3.0/3.1 or GraphQL SDL / `.proto` → JSON Schema → per-provider shape.

**REST adapter.** Map each OpenAPI operation to one tool. Keep arity small: one operation ≠ dump of 200 endpoints (schema tokens dominate; see §2). Bind auth **outside** the model: inject `Authorization` / mTLS / signed cookies in the adapter. Never put secrets in the schema enum. Pagination, retries, and idempotency belong in the adapter, not in the LLM (the model will re-call `list_orders` with `page=1` forever).

**gRPC adapter.** Parse `.proto` (or server reflection) → unary RPCs as tools; mark client/server/bidi streams as **unsupported or as long-running tools** (do not ask the model to consume a stream token-by-token). JSON-to-protobuf mapping is lossy for `oneof`, maps, and default-zero fields — validate with `protojson` and return field-level errors as `tool_result`. gRPC status codes (`UNAVAILABLE`, `RESOURCE_EXHAUSTED`, `DEADLINE_EXCEEDED`) map to retry vs fail-closed.

**GraphQL adapter.** Introspection → one tool per **safe** query/mutation, or one `graphql_query` tool with a **persisted-query allowlist** (unrestricted GraphQL is an RCE-shaped confused-deputy). Pagination is Relay connections: `edges { node, cursor }`, `pageInfo { hasNextPage, endCursor }`, arguments `first`/`after` ([Relay Cursor Connections](https://relay.dev/graphql/connections.htm)). Cost-based rate limits (GitHub-style points) replace simple RPM; the adapter must cap query depth and node count before the model can `first: 10000`.

**Auth in adapters.** Patterns that survive audit: (1) **on-behalf-of** OAuth with RFC 8707 resource indicators so the downstream token is audience-bound to the API, not a passthrough of the MCP/client token ([MCP auth](https://modelcontextprotocol.org/specification/2025-11-25/basic/authorization); [MCP 2026-07-28 security considerations](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/security-considerations)); (2) workload identity (AWS IAM / GCP ADC) for first-party APIs; (3) per-tenant API keys in a secret store, referenced by `tenant_id` the model is allowed to pass. MCP remote HTTP: OAuth 2.1 + PKCE, RFC 9728 Protected Resource Metadata, RFC 8414 / OIDC discovery.

**Idempotency.** RFC 9110: GET/PUT/DELETE are idempotent; POST/PATCH are not. Stripe-class APIs: client sends `Idempotency-Key` (UUIDv4, ≤255 chars); server stores status+body ≥24h and replays it, including 500s; keys not saved if validation fails before execution ([Stripe idempotent requests](https://docs.stripe.com/api/idempotent_requests)). IETF `Idempotency-Key` header draft-07 (expired 2026-04-18) specified `409` in-flight and `422` fingerprint mismatch ([draft-ietf-httpapi-idempotency-key-header-07](https://datatracker.ietf.org/doc/html/draft-ietf-httpapi-idempotency-key-header)). **Agent rule:** derive the key from `(tenant, tool_name, canonical_args_hash, user_intent_id)` **or** from the model’s `call_id`/`tool_use_id` if the runtime guarantees exactly-once delivery of that ID. Do not let the model invent the key (hallucinated keys = duplicate charges).

**Pagination.** Offset (`page`/`offset`) drifts under concurrent writes — ban for agents. Prefer: Stripe list `starting_after` / `ending_before` + `has_more`; Google-style opaque `pageToken`; GitHub `Link: rel="next"` ([RFC 8288](https://www.rfc-editor.org/rfc/rfc8288.html)); GraphQL Relay `after`/`endCursor`. Cap `limit` in the adapter (e.g. max 100) regardless of schema.

**Retries.** Retry only **safe** failures: 408/429/5xx, gRPC `UNAVAILABLE`/`DEADLINE_EXCEEDED`, network reset. Honor `Retry-After`. Exponential backoff + jitter. **Never retry POST without an idempotency key.** Convert 4xx (except 429) to `is_error` tool results so the model can correct args instead of looping.

**Webhook vs poll.** Poll burns RPM and tokens (`get_job` every turn). Prefer: start work → return `{job_id, status:"pending"}` → Temporal/Kafka wait → webhook/signal resumes the workflow → next model turn gets the result. Stripe webhooks: HMAC `Stripe-Signature`, 5-minute skew, at-least-once for up to **3 days** live; dedupe on `event.id` ([Stripe idempotency discussion](https://sujeet.pro/articles/stripe-idempotency-reliability)). E2B lifecycle: `sandbox.lifecycle.killed` / `paused` webhooks carry `execution_time`, `vcpu_count`, `memory_mb`; verify `e2b-signature`, dedupe `e2b-delivery-id` ([E2B price FAQ](https://e2b.dev/docs/faq/calculate-sandbox-price)). Poll only when the vendor has no callback (some CRMs) and then with a **server-side** scheduler, not an LLM loop.

### 1.4 Function calling mechanics (OpenAI / Anthropic / Gemini)

**OpenAI (Responses + Chat Completions).** Tools: `type: function` + JSON Schema + `strict`. `tool_choice`: `"auto"` | `"required"` | `"none"` | `{type:"function", name}` | `allowed_tools` (subset without mutating the cached tools list — preserves prompt cache). `parallel_tool_calls: false` ⇒ 0 or 1 call. GPT-5+ may parallelize functions alongside built-ins, but built-ins stay out of the parallel function batch. `tool_search` + deferred tools: `gpt-5.4`+ ([OpenAI function calling](https://developers.openai.com/api/docs/guides/function-calling); [Azure FC](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/how-to/function-calling)). Strict mode = Structured Outputs; incompatible schemas **reject** if `strict: true`. Responses **omit `strict`**: try to normalize, else fall back (`strict: false` on the tool). Chat Completions default non-strict. Fine-tuned models: parallel calls **disable strict** for that turn. Injection: execute → `function_call_output` / Chat Completions `role: tool` + `tool_call_id`.

**Anthropic.** Client tools: `stop_reason: "tool_use"`, one or more `tool_use` blocks. Return **all** `tool_result` blocks in **one** user message, `tool_result` **before** any text, match `tool_use_id`; skipped calls still need `is_error: true` ([parallel tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/parallel-tool-use)). `tool_choice`: `{type:"auto"|"any"|"tool"|"none"}`; `disable_parallel_tool_use: true` is **inside** `tool_choice`, not top-level. Server tools run on Anthropic unless mixed with client tools. Computer/browser are **client toolsets** (`computer_toolset_20260801`, `browser_toolset_20260801`) — one `type` entry expands to many member tools; execute members **in order** for a batch action ([computer use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool); [tool reference](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-reference)).

**Gemini.** `tool_choice` / `FunctionCallingConfig.mode`: `AUTO` | `ANY` | `NONE` | `VALIDATED` (preview: schema adherence; Gemini 3+ also enforces required params) ([Gemini FC](https://ai.google.dev/gemini-api/docs/function-calling); [Enterprise intro](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/tools/function-calling)). Every `functionCall` has a unique `id` that **must** be echoed on `functionResponse`. Parallel calls supported. Remote MCP: `name` + `url` in tools config (Interactions). `gemini-3.1-pro-preview-customtools` exists for mixed bash + custom tools ([Gemini tools](https://ai.google.dev/gemini-api/docs/tools)).

**Hallucinated params.** Non-strict decoding can invent keys, drop required fields, or emit invalid JSON. Mitigations: OpenAI `strict: true`, Gemini `VALIDATED`/`ANY`, server-side JSON Schema validation **before** side effects, enum/allowlists in the adapter, and BFCL-style irrelevance tests (abstain when no tool fits) ([BFCL V4](https://gorilla.cs.berkeley.edu/leaderboard); [BFCL ICML 2025](https://proceedings.mlr.press/v267/patil25a.html)).

### 1.5 Browser topology (browser-use / Playwright / Computer Use)

Three observation/action channels:

| Channel | Observation | Action | Typical stack |
| --- | --- | --- | --- |
| **A11y / DOM snapshot** | Accessibility tree + refs (`e5`) | Click/type by ref | Playwright MCP default |
| **Screenshot / Computer Use** | PNG/JPEG pixels; model returns coordinates | Click/type/scroll in VM or Playwright | Anthropic computer/browser toolsets; OpenAI `computer`; Gemini `computer_use` |
| **Hybrid agent** | Snapshot + optional screenshot | LLM chooses next action; Playwright executes | browser-use on CDP |

**Playwright MCP** (Microsoft): 40+ tools; **snapshot-first** — official comparison ~**200–400 tokens**/snapshot vs ~**3,000–5,000 tokens** for screenshots; actions must use snapshot refs, not screenshot pixels ([Playwright MCP intro](https://playwright.dev/mcp/introduction); [snapshots](https://playwright.dev/mcp/snapshots); [GitHub](https://github.com/microsoft/playwright-mcp)). Isolation: Playwright `BrowserContext` = incognito profile (cookies, localStorage, cache) ([Playwright isolation](https://playwright.dev/docs/browser-contexts)). Timeouts: action **5s**, navigation **60s**, expect **5s** defaults ([MCP options](https://playwright.dev/mcp/configuration/options)). `--allowed-origins` / `--blocked-origins`: **not a security boundary and does not affect redirects** ([Playwright MCP README](https://github.com/microsoft/playwright-mcp)). `--isolated` vs addressable `sessionId` contexts: one browser process, N contexts.

**browser-use**: Python agent loop — `get_browser_state_summary(include_screenshot=True)` → LLM `AgentOutput` → `tools.act` ([browser-use](https://github.com/browser-use/browser-use); [docs agents](https://browser-use-browser-use.mintlify.app/concepts/agents)). Shares Chrome with Playwright via CDP ([Playwright integration](https://docs.browser-use.com/open-source/examples/templates/playwright-integration)). Domain allowlists belong in `Browser`/`BrowserSession` config (library-level; still not a kernel boundary).

**Anthropic Computer Use (GA toolset `computer_toolset_20260801`, no beta header on Claude API):** 17 members (`screenshot`, `left_click`, `type`, `zoom`, …); client-hosted VM/container; batch actions **in order**; not in Claude Managed Agents. Prefer `browser_toolset_20260801` for webpage-only work (DOM/a11y members, no full desktop). Screenshot classifiers can force user confirmation on suspected injection ([computer use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool)).

**OpenAI Computer Use:** Responses `computer` tool; `computer_call.actions[]` run in order; return `computer_call_output` with `computer_screenshot`; prefer `detail: "original"`; GPT-5.6 does **not** resize original images — large screenshots consume unbounded input tokens; observed working desktop sizes 1440×900 and 1600×900 ([OpenAI computer use](https://developers.openai.com/api/docs/guides/tools-computer-use)). `computer-use-preview` remains a specialized Responses-only model ([model page](https://developers.openai.com/api/docs/models/computer-use-preview)).

**Gemini Computer Use:** client loop; screenshots in, `function_call` UI actions out; `ENVIRONMENT_BROWSER` (default) / `MOBILE` / `DESKTOP`; Playwright or Browserbase in the reference impl; Gemini 3.x may attach `intent` + `safety_decision` (`require_confirmation` / blocked) ([Gemini computer use](https://ai.google.dev/gemini-api/docs/computer-use); [DeepMind announcement](https://blog.google/innovation-and-ai/models-and-research/google-deepmind/gemini-computer-use-model/); [preview repo](https://github.com/google-gemini/computer-use-preview)).

**Allowlists & session isolation (required for all three):** dedicated VM/container; per-task `BrowserContext`; sticky proxy bound to context if geo matters; no raw credentials in the model context; HITL for checkout/ToS/cookies ([Anthropic computer-use security](https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool); [OpenAI computer use untrusted content](https://developers.openai.com/api/docs/guides/tools-computer-use)).

### 1.6 Code execution topology (sandbox + limits)

| Runtime | Isolation | Kernel | Typical cold path | GPU | Notes |
| --- | --- | --- | --- | --- | --- |
| **Firecracker** | Hardware VM (KVM) | Guest Linux | Spec: **≤125 ms** InstanceStart → guest `/sbin/init`; ≤5 MiB VMM overhead; >95% bare-metal compute [pending test] ([SPECIFICATION.md](https://github.com/firecracker-microvm/firecracker/blob/448df604b0ff8c3c9e7e98cb7808dd51c25a1d58/SPECIFICATION.md); [firecracker.io](https://firecracker-microvm.github.io/)) | No passthrough in E2B | AWS Lambda, E2B, Lambda MicroVMs (2026-06) |
| **gVisor `runsc`** | Userspace Sentry + Gofer | Host kernel, ~50 syscalls | Process-start, no guest boot ([gVisor](https://gvisor.dev); [Northflank explainer](https://northflank.com/blog/what-is-gvisor)) | nvproxy CUDA path | GKE Sandbox, Cloud Run |
| **WASM/WASI** | Capability-based runtime (Wasmtime / WasmEdge) | None | Sub-ms module start [inferred from runtime design; ⚠️ no vendor p99 in this brief] | N/A | WASI 0.2 component model; not a full Linux ABI |
| **E2B** | Firecracker microVM | Guest Linux | Vendor marketing ~150 ms; pause/resume; no GPU ([e2b.dev](https://e2b.dev/); [billing](https://e2b.dev/docs/billing)) | No | Internet from sandbox by default — treat as egress risk |
| **OpenAI Code Interpreter / Hosted Shell** | Provider VM | Debian 12 (shell docs; may change) | Auto or `/v1/containers`; idle **20 min**; memory **1g/4g/16g/64g** ([code interpreter](https://developers.openai.com/api/docs/guides/tools-code-interpreter); [shell](https://developers.openai.com/api/docs/guides/tools-shell); [community: expired auto](https://community.openai.com/t/should-auto-spawn-a-new-container-after-timeout/1291625)) | No | **No egress by default**; org allowlist + `network_policy` |
| **Anthropic `code_execution`** | Anthropic container | Unspecified public ABI | Server tool; 5 min **billing** minimum when charged ([pricing](https://platform.claude.com/docs/en/about-claude/pricing)) | No | Free if `web_search_20260209+` or `web_fetch_20260209+` in the same request |
| **Gemini code execution** | Google sandbox | Python only | **30 s** max; up to **5** regenerate-on-error; no arbitrary file I/O ([code execution](https://ai.google.dev/gemini-api/docs/generate-content/code-execution)) | No | No extra tool fee |
| **AWS Lambda MicroVMs** (2026-06) | Firecracker | Guest | Snapshot resume; suspend/resume up to **8 h**; ARM64; up to **16 vCPU / 32 GB RAM / 32 GB disk** in launch coverage ([AWS News](https://aws.amazon.com/blogs/aws/run-isolated-sandboxes-with-full-lifecycle-control-aws-lambda-introduces-microvms/); [what's new](https://aws.amazon.com/about-aws/whats-new/2026/06/aws-lambda-microvms/); [InfoQ](https://www.infoq.com/news/2026/06/aws-lambda-microvms/)) | ⚠️ not claimed in launch notes | Integrates as Anthropic self-hosted sandbox backend |

**Measured Firecracker vs spec (NumaVM, 2026-03-10, not AWS SLA):** spec 125 ms is InstanceStart→`init` only. End-to-end cold **SSH-ready 1,133 ms**; snapshot restore **176 ms** (snapshot load 25 ms) ([NumaVM](https://numavm.com/blog/2026-03-10-1-second-boot/)). Use snapshot restore for agent “warm sandbox” UX.

**Network egress.** OpenAI hosted containers: deny-by-default; dashboard org allowlist **and** request `network_policy` ([shell network](https://developers.openai.com/api/docs/guides/tools-shell)). E2B: sandboxes can reach the internet ([E2B product](https://e2b.dev/)). Gemini CI: no extra network product surface in the public code-execution docs (assume locked). Always pin DNS between allowlist check and connect (TOCTOU / rebinding — MCP security docs).

---

## 2. Token Economics & NFR Metrics

### 2.1 Model token rates used in this section (USD / 1M tokens)

**OpenAI (openai.com/api/pricing, 2026-08):** GPT-5.6 Sol **$5 / $0.50 cached / $30 out**; Terra **$2 / $0.20 / $12**; Luna **$0.20 / $0.02 / $1.20**. Batch **50%**. GPT-5.6+ prompt-cache **writes 1.25×** uncached input, reads at cached rate, **30 m TTL**, min prefix **1,024** tokens; cached tokens **still count toward TPM** ([prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching); [API pricing](https://openai.com/api/pricing/); [docs pricing](https://developers.openai.com/api/docs/pricing)).

**Anthropic ([pricing](https://platform.claude.com/docs/en/about-claude/pricing)):** Sonnet 5 **$2 in / $10 out** (introductory $2/$10 is now standard; the Sep 2026 rise to $3/$15 **will not occur**). Opus 5 / 4.8 **$5 / $25**. Haiku 4.5 **$1 / $5**. Cache: 5 m write **1.25×**, 1 h write **2×**, hit **0.1×**. US `inference_geo` on 4.6+ **1.1×**. Batch **50%**. Fast mode Opus 5/4.8 **$10 / $50**. Claude 4.7+ tokenizer ≈ **+30% tokens** vs prior for the same text.

**Gemini ([pricing](https://ai.google.dev/gemini-api/docs/pricing)):** 3.6 Flash **$1.50 / $7.50**; 3.5 Flash **$1.50 / $9.00**; 3.1 Pro Preview **$2 / $12** (≤200k) and **$4 / $18** (>200k). Grounding with Google Search (Gemini 3): 5,000 prompts/month free shared, then **$14 / 1,000 search queries** (a prompt may emit **multiple** queries). 2.5 Pro Search: **$35 / 1,000 grounded prompts** after 1,500 RPD free. Batch/Flex ~50% on listed SKUs.

**Tool-definition overhead (Anthropic, billed as input):** tool-use system prompt **286–675 tokens** (`auto`) vs **406–804** (`any`/`tool`) depending on model; computer toolset default **~4,500** input tokens; browser toolset default **~6,600**; bash extra **244–325**; `text_editor_20250429` **+700** ([pricing](https://platform.claude.com/docs/en/about-claude/pricing)). OpenAI/Gemini: schemas also sit in the prompt prefix — **pin tool order and text** for cache hits ([OpenAI caching](https://developers.openai.com/api/docs/guides/prompt-caching); Anthropic cache hits **do not count against ITPM** on current models per Anthropic caching docs ([prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)) — opposite of OpenAI).

### 2.2 Built-in tool fees (not token-only)

| Tool | Fee (2026-08 docs) | Plus tokens? | Source |
| --- | --- | --- | --- |
| OpenAI Web Search | **$10.00 / 1k calls** | Search content tokens at model rates; some mini SKUs bill 8k input block | [OpenAI pricing](https://developers.openai.com/api/docs/pricing) |
| OpenAI File Search | **$2.50 / 1k calls** + **$0.10 / GB-day** storage (1 GB free) | Yes | same |
| OpenAI Containers (CI + Hosted Shell) | **1 GB $0.03**, **4 GB $0.12**, **16 GB $0.48**, **64 GB $1.92** per **20-minute session** per container; eligible sessions **per-minute with 5-minute minimum** | Tokens at model rates | [openai.com/api/pricing](https://openai.com/api/pricing/); [docs pricing](https://developers.openai.com/api/docs/pricing) |
| Anthropic Web Search | **$10 / 1k searches**; errors not billed | Results are input tokens every later turn | [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing) |
| Anthropic Web Fetch | **$0** extra | ~2,500 tok / 10 kB page; ~25k / 100 kB; ~125k / 500 kB PDF | same |
| Anthropic Code Execution | **$0** with web_search/web_fetch ≥20260209; else **$0.05 / container-hour**, **5 min min**, **1,550 free hours/org/month**; files preload ⇒ billed even if unused | stdout/stderr as tokens | same |
| Anthropic Managed Agents | **$0.08 / running session-hour** (idle not billed) + tokens; web search still $10/1k | Yes | same |
| Gemini Code Execution | **$0** extra | Generated code + execution output billed as tokens; intermediate tokens labeled in response | [Gemini CI](https://ai.google.dev/gemini-api/docs/generate-content/code-execution) |
| Gemini Search grounding | **$14 / 1k queries** (Gemini 3, after free) | Plus model tokens | [Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing) |
| E2B compute | **$0.000014 / vCPU-s** = **$0.0504 / vCPU-h**; **$0.0000045 / GiB-s** = **$0.0162 / GiB-h**; paused/killed **$0** | N/A | [E2B FAQ](https://e2b.dev/docs/faq/calculate-sandbox-price); [pricing](https://www.e2b.dev/pricing) |

E2B default sandbox **2 vCPU + 512 MiB**: **~$0.109 / hour**. Hobby: $0 + $100 one-time credits, **1 h** max, **20** concurrent, **1 create/s**. Pro: **$150/mo** + usage, **24 h**, **100–1,100** concurrent, **5 create/s**. Disk included 10/20 GiB.

### 2.3 `$ per 1k executions` (tool-fee line only, excluding model tokens unless noted)

| Workload | Assumptions | **$ / 1k executions** |
| --- | --- | --- |
| OpenAI / Anthropic **web search** tool call | Fee only | **$10.00** |
| OpenAI **file search** query | Fee only | **$2.50** |
| Gemini 3 **Search query** (paid, after free) | Fee only | **$14.00** |
| Gemini 2.5 Pro **grounded prompt** (paid) | Fee only | **$35.00** |
| OpenAI CI **1 GB** session | 1k sessions × $0.03 / 20 min | **$30.00** |
| OpenAI CI **4 GB** | 1k × $0.12 | **$120.00** |
| OpenAI CI **16 GB** | 1k × $0.48 | **$480.00** |
| OpenAI CI **64 GB** | 1k × $1.92 | **$1,920.00** |
| Anthropic code exec (paid path) | 1k × 5 min min × $0.05/h = 1k × $0.004167 | **$4.17** (then tokens) |
| Anthropic Managed Agents runtime | 1k × 1 min running × $0.08/h | **$1.33** |
| E2B default, **5 s** run | $0.109/h × 5/3600 × 1k | **~$0.15** |
| E2B default, **60 s** | ×12 | **~$1.82** |
| E2B default, **1 h** | | **~$109** |
| Pure REST GET tool | Your API cost only; LLM still pays schema+result tokens | **~$0.00** tool fee |

**Browser vs text (token side, Playwright MCP published ranges):** snapshot **~200–400 tokens** vs screenshot **~3,000–5,000** ([snapshots](https://playwright.dev/mcp/snapshots)). Mintlify Playwright MCP copy cites screenshot **10k–50k** vs snapshot **500–5,000** — treat as a wider band, same direction ([a11y snapshots](https://microsoft-playwright-mcp.mintlify.app/concepts/accessibility-snapshots)). Anthropic computer toolset **~4.5k** tokens **before** the first screenshot; browser toolset **~6.6k**. A 20-step screenshot loop on Sonnet 5 is dominated by **image input**, not the 4.5k prefix. ⚠️ Anthropic vision $/megapixel is on the Vision page — do not guess; count `usage` per turn.

**Worked LLM+tool example [inferred composition from list prices]:** Sonnet 5, 8k cached-hit input (`$0.20/MTok`) + 4k uncached (`$2`) + 800 out (`$10`) + 1 Anthropic web search (`$0.01`): input $0.0016 + $0.008 + $0.008 + $0.01 ≈ **$0.028 / turn**. Same turn with OpenAI web search is the same **$0.01** search fee plus OpenAI token SKU. A Playwright-MCP snapshot turn at 300 tokens extra input on Sonnet 5 is **$0.0006**; a 4k-token screenshot is **$0.008** input — **~13×** the snapshot increment.

### 2.4 RPM / TPM / ITPM / OTPM

**OpenAI ([rate limits](https://developers.openai.com/api/docs/guides/rate-limits)):** org + project, not user. Dimensions: RPM, TPM, RPD, TPD (plus IPM / audio). Tiers by cumulative spend: Free / T1 **$5** / T2 **$50** / T3 **$100** / T4 **$250** / T5 **$1,000** paid; usage caps $100 → $200k/month at T5. **Dashboard + `x-ratelimit-*` headers are source of truth** — public tables lag. Secondary compilations (verify in dashboard): GPT-4o-class T1 often cited **500 RPM / 30k TPM**, T5 **10k RPM / 30M TPM** ([Inference.net guide](https://inference.net/content/openai-rate-limits-guide/)).

**Anthropic:** RPM + **ITPM** + **OTPM** (split). `429` = your limit (`retry-after`); **`529 overloaded_error`** = provider saturation, not your quota. Cache reads on current models **exempt from ITPM** ([prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)). ⚠️ Published per-tier tables disagree across third-party blogs; use Console limits.

**Gemini:** `RESOURCE_EXHAUSTED` (429); per-minute **and** per-day quotas ([billing](https://ai.google.dev/gemini-api/docs/billing.md.txt) — tiers Free / T1 $250 cap / T2 $2k / T3 $20k–$100k).

**E2B:** create-rate **1/s** Hobby, **5/s** Pro; concurrency 20 / 100–1,100 ([billing](https://e2b.dev/docs/billing)).

**Downstream REST:** Stripe/GitHub/etc. have their own RPM; the tool adapter needs a **separate** token bucket so the agent cannot stampede a SaaS API.

### 2.5 Latency NFRs (published numbers only)

⚠️ **No vendor publishes p50/p95/p99 for “tool dispatcher” as a product SLO.** Use these as engineering bounds, not SLAs:

| Stage | Published figure | Kind |
| --- | --- | --- |
| Firecracker InstanceStart → init | **≤125 ms** | Spec bound ([SPEC](https://github.com/firecracker-microvm/firecracker/blob/448df604b0ff8c3c9e7e98cb7808dd51c25a1d58/SPECIFICATION.md)) |
| Firecracker host create rate | **up to 150 microVMs/s/host** | Spec ([firecracker.io](https://firecracker-microvm.github.io/)) |
| Firecracker cold SSH-ready | **1,133 ms** | NumaVM lab ([blog](https://numavm.com/blog/2026-03-10-1-second-boot/)) |
| Firecracker snapshot restore SSH | **176 ms** | same |
| Playwright MCP action / nav | **5 s / 60 s** defaults | Timeouts, not latency ([options](https://playwright.dev/mcp/configuration/options)) |
| Gemini code sandbox | **30 s** hard cap | Deadline ([CI docs](https://ai.google.dev/gemini-api/docs/generate-content/code-execution)) |
| OpenAI container idle TTL | **20 min** | Lifecycle ([CI](https://developers.openai.com/api/docs/guides/tools-code-interpreter)) |
| OpenAI strict schema first compile | ⚠️ “1–2 s extra” | Third-party (Cadence); not in official docs |
| Stripe webhook signature skew | **5 min** | Security window |

**p95/p99 [inferred ops targets, not vendor SLOs]:** REST tools: p95 **<800 ms** including auth; browser step p95 **<8 s** (action timeout 5 s + LLM); sandbox exec p95 **< the product cap** (30 s Gemini, 20 min OpenAI idle, E2B session length). Instrument `tool_exec_ms` histograms yourself.

**Caching / routing.** Stable **tools JSON first** in the prompt. OpenAI: `prompt_cache_key` + `allowed_tools` to change eligibility without busting the tools prefix. Anthropic: `cache_control` on tools/system; 5 m vs 1 h write multiplier. Gemini: explicit cache objects + **$/MTok-hour storage** (3.1 Pro **$4.50 / MTok-hour**). Route cheap models (Luna / Haiku / 3.6 Flash) for schema-valid CRUD; reserve Opus/Sol/3.1 Pro for computer-use and ambiguous tool choice.

---

## 3. Distributed Resilience & State

### 3.1 Durable tool execution (Temporal vs Kafka)

**Temporal rule:** Workflows are deterministic replay. **Every LLM call and every tool I/O is an Activity** ([Temporal AI reference architecture](https://go.temporal.io/platform-hub/ai-engineering/ai-reference-architecture)). OpenAI Agents SDK + Temporal: orchestration in workflow; model/tools/sandbox lifecycle as activities; `activity_as_tool`; sandbox session serialized so worker crash ≠ lost VM handle ([Temporal announcement](https://temporal.io/blog/announcing-openai-agents-sdk-integration); [sandbox blog](https://temporal.io/blog/introducing-temporal-and-agentic-sandboxes-openai-agents-sdk); [contrib README](https://github.com/temporalio/sdk-python/blob/53ae9fc7/temporalio/contrib/openai_agents/README.md)).

**Kafka role:** ingest webhooks and high-volume “job completed” events; **do not** put Kafka clients inside Workflow code. Pattern: webhook → Kafka → consumer `Signal`/`Update` Temporal. At-least-once ⇒ idempotency keys on the Activity.

**LangGraph:** node-level checkpointer is durable *conversation* state, not a distributed lock around Stripe POST — still wrap side-effect tools in an external workflow or outbox.

### 3.2 Timeouts, circuit breakers, rate limits

**Timeout budget (nested, fail-closed):** LLM request timeout > tool Activity `StartToCloseTimeout` > HTTP client timeout > downstream SLA. Computer-use loops need a **wall-clock ceiling** (e.g. 5 min task envelope — [inferred] from common Playwright agent practice; Anthropic does not publish one). Gemini CI **30 s**. Playwright nav **60 s**. OpenAI container **20 min idle** (not an exec timeout).

**Circuit breaker:** per **downstream API** and per **sandbox pool**, not per model. Open after N consecutive 5xx/timeouts; half-open with a probe. Feed `is_error` to the model **once**, then short-circuit further calls to that dependency for the cooldown. Combine with RPM token buckets (OpenAI 429, Anthropic 429 vs 529 — **failover provider on 529**, backoff on 429).

**Partial parallel failure:** Anthropic/OpenAI/Gemini can emit N parallel calls. Execute with `gather` + per-call try/catch; always return N results. One 500 must not drop sibling `tool_use_id`s ([Anthropic parallel](https://platform.claude.com/docs/en/agents-and-tools/tool-use/parallel-tool-use)).

### 3.3 Sandbox lifecycle

States: **image/template build → create → running → (pause/snapshot) → resume → kill/expire**. E2B: bill running only; pause does not bill; Hobby 1 h / Pro 24 h hard caps; webhook on killed/paused for cost ([E2B FAQ](https://e2b.dev/docs/faq/calculate-sandbox-price)). OpenAI: auto reuse vs explicit `container_id`; **expired container + `previous_response_id` fails** rather than auto-recreate ([community](https://community.openai.com/t/should-auto-spawn-a-new-container-after-timeout/1291625)). Lambda MicroVMs: Dockerfile → snapshot image → HTTPS URL (HTTP/2, gRPC, WebSockets); idle suspend with memory+disk. Anthropic Managed Agents: `$0.08/h` only while `running`; idle wait is free. Always persist **artifacts to object storage** before TTL; never treat `/mnt/data` as durable.

**Idempotent sandbox ops:** `create` with a client-generated sandbox key; retries must not leak VMs. Cap concurrent sandboxes to E2B/OpenAI quotas or you will 429 the control plane while Temporal retries Activities.

---

## 4. Enterprise Security & Governance

### 4.1 Zero-Trust MCP

MCP 2025-11-25 / 2026-07-28: remote servers are **OAuth 2.1 resource servers**. MUST: RFC 9728 PRM, audience-bound tokens (RFC 8707 `resource`), PKCE, no implicit grant, exact redirect URIs. MUST NOT: **token passthrough** to upstream APIs — exchange for a new scoped token. Proxies MUST **per-client consent** (confused deputy: static client_id + DCR + consent cookie). June 2026 **Enterprise-Managed Authorization** (ID-JAG, optional extension) for org SSO without per-server consent screens ([MCP authorization](https://modelcontextprotocol.org/specification/2025-11-25/basic/authorization); [security best practices](https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/security_best_practices); [security considerations](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/security-considerations); [Auth0 June 2025](https://auth0.com/blog/mcp-specs-update-all-about-auth/)). Local STDIO: env credentials, not OAuth. HTTP localhost: bind `127.0.0.1`, validate `Origin`/`Host` (DNS rebinding).

### 4.2 Tool RBAC, PII, audit

**RBAC belongs in the dispatcher**, not in the system prompt. Map `(principal, tenant, tool, args_shape)` → allow/deny/HITL. High-impact: payments, email send, `puppeteer_evaluate`, shell, computer click. ADK `LongRunningFunctionTool` and Agents SDK approval hooks are the productization of HITL. **PII:** tool results re-enter the context window — redact or summarize before injection; do not log raw `tool_result` to third-party traces without a BAA. Computer-use screenshots are PII-dense; Anthropic ZDR eligibility is documented per model with screenshot retention caveats in product privacy articles (verify current ZDR table at request time).

**Audit:** persist `tool_name`, `call_id`, principal, hashed args, policy decision, downstream status, latency, sandbox id. Temporal history is a natural audit log for Activities. OpenAI Agents SDK tracing + Bedrock `requestMetadata` are not a substitute for an immutable SIEM trail.

### 4.3 Sandbox isolation and SSRF

Prefer Firecracker/Lambda MicroVMs for **untrusted model-generated code**. gVisor: Sentry exploit + remaining ~50 host syscalls. WASM: strongest capability model, weakest Linux compatibility. OpenAI CI: no egress until dual control (admin allowlist + `network_policy`). **SSRF:** browser `navigate` and `web_fetch` / custom HTTP tools must deny link-local **169.254.169.254**, RFC1918, localhost, and metadata IPv6. Playwright MCP origin lists **do not stop redirects**. MCP `server-puppeteer` advisory: navigate + screenshot/evaluate = SSRF + XSS-in-agent ([GitHub issue 3660](https://github.com/modelcontextprotocol/servers/issues/3660)). Pin DNS; do not trust URL hostname alone.

### 4.4 Prompt injection via tool results

OWASP: **LLM01** prompt injection, **LLM06** excessive agency; Agentic **ASI01** goal hijack, **ASI02** tool misuse, **ASI05** unexpected code execution ([OWASP cheat sheet](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html); [MCP Tool Poisoning](https://owasp.org/www-community/attacks/MCP_Tool_Poisoning); [CSA IPI 2026](https://labs.cloudsecurityalliance.org/research/csa-research-note-indirect-prompt-injection-in-the-wild-2026/)). MCP poisoning: benign `tools/list` descriptions, malicious **runtime responses**. Dual-LLM: quarantined model reads untrusted pages/tool JSON; privileged model only sees structured summaries and holds the tools. Action-screening guardrail: compare proposed tool call to **original user intent**, not to the poisoned observation. Anthropic computer-use **screenshot classifiers** + confirmation; OpenAI: treat all page text/screenshots as untrusted ([computer use](https://developers.openai.com/api/docs/guides/tools-computer-use)). Empirical: InjecAgent / 2026 MCP-client study — prompt-only defenses fail ([arXiv 2603.21642](https://arxiv.org/html/2603.21642v1)).

---

## 5. Production Failure Modes

### 5.1 Hallucinated parameters

**Symptoms:** extra keys, wrong enums, fabricated IDs (`order_id: "12345"`), invalid JSON, parallel duplicate calls (`gpt-4.1-nano-2025-04-14` documented duplicate-same-tool issue — disable parallel). **BFCL V4** weights hallucination/irrelevance **10%** of overall (1,122 samples in the published breakdown) ([BFCL V4 web search blog](https://gorilla.cs.berkeley.edu/blogs/15_bfcl_v4_web_search.html); [leaderboard updated 2026-04-12](https://gorilla.cs.berkeley.edu/leaderboard)). **Fix:** strict/validated decoding + adapter validation + canonical ID lookup (never trust model-supplied PKs) + return schema errors as tool results (one retry) then fail.

### 5.2 Infinite tool loops

Causes: poll-instead-of-webhook; error results that say “try again” without a budget; computer-use never emitting stop; Gemini CI regenerating up to **5** times on sandbox errors; agent frameworks without `max_turns`. **Fix:** hard turn cap; loop detector on (tool, args_hash) repeats; Playwright MCP / browser-use history; Temporal `WorkflowTaskTimeout`; `tool_choice: none` on the closing turn; circuit-break identical GET pagination.

### 5.3 Browser hangs

SPA never idle, file download dialogs, auth walls, CAPTCHA, `page.waitFor` forever. Defaults: Playwright action **5 s**, navigation **60 s**. Computer Use coordinate mismatch after CSS zoom. **Fix:** task wall clock; screenshot+a11y hybrid; abort `evaluate`/JS tools on untrusted pages; don’t share one context across users (cookie bleed looks like “hangs” on the wrong session). Origin allowlists **won’t** catch open-redirect chains.

### 5.4 Sandbox escape (and lookalikes)

True escape: guest kernel + VMM bug (Firecracker) or Sentry + host syscall (gVisor). **More common:** egress to IMDS; shared Docker socket; `puppeteer_evaluate` as in-browser RCE; WASM host functions over-granted. OpenAI: enabling network **explicitly** expands prompt-injection via fetched content ([shell security](https://developers.openai.com/api/docs/guides/tools-shell)). E2B internet-on-by-default is a **policy** choice, not a CVE. Lambda MicroVMs exist because container isolation was judged insufficient for AI-generated code ([AWS News](https://aws.amazon.com/blogs/aws/run-isolated-sandboxes-with-full-lifecycle-control-aws-lambda-introduces-microvms/)).

### 5.5 Cascading timeouts

LLM timeout 60 s → tool HTTP 55 s → sandbox 30 s → downstream 25 s, all firing retries: **retry amplification**. OpenAI auto container expire mid-thread. Anthropic 529 mistaken for 429. Parallel tools sharing one global HTTP pool. **Fix:** one retry owner (Temporal Activity policy); idempotency keys; bulkhead thread pools per tool class; on 529 switch model provider; on container expiry **start a new container** and tell the model files were lost; never `previous_response_id` against a dead OpenAI container.

---

## 6. Enterprise System Design Scenarios

### 6.1 Reference topology (scale)

```
API Gateway ─► Agent Orchestrator (Temporal Workflow)
                 ├─ Activity: LLM (OpenAI/Anthropic/Gemini/Bedrock)  [RPM/ITPM circuit]
                 ├─ Activity: ToolDispatcher
                 │    ├─ Policy (RBAC, schema, SSRF URL, PII redact)
                 │    ├─ REST/gRPC/GraphQL adapters (idempotency, cursor page, Retry-After)
                 │    ├─ MCP gateway (OAuth 2.1, audience, no passthrough)
                 │    ├─ Browser pool (Playwright contexts; snapshot-first)
                 │    └─ Sandbox pool (E2B / Lambda MicroVM / OpenAI container)
                 ├─ Kafka: webhooks in / audit out
                 └─ Object store: sandbox artifacts, HAR, screenshots
```

Capacity sketch **[inferred from published limits, not a vendor sizing guide]:** 100 concurrent agents × 4 parallel tools × 2 s avg REST = 200 in-flight HTTP; needs HTTP pool + downstream RPM. Same 100 agents on E2B default ⇒ **100 concurrent sandboxes** = Pro floor (or 5 Hobby accounts — don’t). 100 OpenAI 1 GB CI sessions = **$3.00 / 20 min** = **$9/h** container line before tokens. 100 Anthropic computer-use threads × ~4.5k toolset tokens × Sonnet 5 $2/MTok ≈ **$0.90 / turn** of prefix **if uncached**; cache hits at 0.1× → **$0.09 / turn** prefix.

### 6.2 Scenario A — Internal SaaS copilot (REST + GraphQL, no browser)

**Choice:** native function calling + OpenAPI adapter; `strict: true` / Gemini `VALIDATED`; MCP only for third-party SaaS with OAuth. **Poll:** forbidden; Jira/Stripe-class jobs via webhook→Temporal. **Scale:** Haiku/Luna/Flash for tool choice; cache the OpenAPI-derived tool list. **Case pattern:** Bedrock Converse + AgentCore for AWS-centric IAM ([Bedrock tool use](https://docs.aws.amazon.com/bedrock/latest/userguide/tool-use.html)). CrewAI if the org wants role crews and a separate `function_calling_llm`.

### 6.3 Scenario B — Research / web agent (browser)

**Choice:** Playwright MCP **snapshot** path for forms and extraction (token and latency); Computer Use only when the UI has no a11y tree (canvas). Isolate per task context; allowlist **plus** redirect-aware fetch proxy. **Cost:** prefer 200–400 tok snapshots over 3k–50k screenshots; Anthropic browser toolset +6.6k prefix — cache it. **Failure:** CAPTCHA and ToS → HITL (`safety_decision` / Anthropic classifiers). Gemini reference: Playwright local or Browserbase ([computer-use-preview](https://github.com/google-gemini/computer-use-preview)).

### 6.4 Scenario C — Code interpreter / data analysis

**Choice matrix:**

| Need | Pick | Why |
| --- | --- | --- |
| Python 30 s, token-only billing | Gemini CI | $0 tool fee, 30 s cap |
| Pandas + files, managed, no egress | OpenAI CI 1–4 GB | $0.03–$0.12 / 20 min; network off |
| Full Linux, pause/resume, internet | E2B | $0.109/h default; Firecracker |
| Enterprise VPC, snapshot resume | Lambda MicroVMs | 8 h suspend; AWS IAM |
| Untrusted + Search in one Claude turn | Anthropic CE + web_* | CE **free** with those tools |

Perplexity/Manus-style products publicly cite E2B for agent VMs ([E2B blog](https://www.e2b.dev/blog/customize-sandbox-compute)). Persist outputs to S3/GCS before 20 min / 1 h / 24 h TTLs.

### 6.5 Trade-off matrix (architect review)

| Axis | Text API tools | Snapshot browser | Screenshot computer use | Firecracker sandbox | WASM sandbox |
| --- | --- | --- | --- | --- | --- |
| Token cost | Low (schema + JSON) | Low–med (200–400/step) | High (images + 4.5–6.6k prefix) | Low (stdout) + session fee | Low |
| p95 latency ⚠️ | Adapter-bound | 5–60 s timeouts | Many LLM rounds | 125 ms–1.1 s cold; 176 ms snap | Fast start |
| Isolation | App authz | Context + VM | Dedicated VM required | Strongest common | Strong, poor ABI |
| SSRF/IPI | URL allowlist | Redirects bypass origin lists | Page injection + click | Egress policy | Host funcs |
| $/1k exec (fee) | ~$0 + SaaS | Infra | Infra + vision tokens | E2B ~$0.15–$109 by duration | Infra |
| Vendor lock-in | Low if OpenAPI | Playwright portable | High (toolset versions) | Medium (E2B/AWS) | Low |

### 6.6 Capacity checklist

1. **Cache** tool schemas (Anthropic ITPM relief; OpenAI cost relief but TPM still counts).  
2. **Defer** tools (`tool_search` / MCP `deferLoading`) once catalogs exceed cache-friendly size.  
3. **Bulkhead** browser, sandbox, and REST pools.  
4. **Idempotency** on every POST the model can double-fire under retry.  
5. **Turn caps** and **idempotent webhooks**.  
6. **Measure** your own p50/p95/p99 on `llm_ms`, `tool_ms`, `sandbox_ms` — vendors will not.

---

## Sources

1. https://developers.openai.com/api/docs/guides/function-calling
2. https://developers.openai.com/api/docs/pricing
3. https://openai.com/api/pricing/
4. https://developers.openai.com/api/docs/guides/prompt-caching
5. https://developers.openai.com/api/docs/guides/rate-limits
6. https://developers.openai.com/api/docs/guides/tools-code-interpreter
7. https://developers.openai.com/api/docs/guides/tools-shell
8. https://developers.openai.com/api/docs/guides/tools-computer-use
9. https://developers.openai.com/api/docs/models/computer-use-preview
10. https://learn.microsoft.com/en-us/azure/ai-foundry/openai/how-to/function-calling
11. https://openai.github.io/openai-agents-js/guides/tools/
12. https://openai.github.io/openai-agents-python/mcp/
13. https://openai.github.io/openai-agents-python/ref/tool/
14. https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview
15. https://platform.claude.com/docs/en/agents-and-tools/tool-use/parallel-tool-use
16. https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool
17. https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-reference
18. https://platform.claude.com/docs/en/agents-and-tools/tool-use/programmatic-tool-calling
19. https://platform.claude.com/docs/en/about-claude/pricing
20. https://platform.claude.com/docs/en/build-with-claude/prompt-caching
21. https://platform.claude.com/docs/en/build-with-claude/handling-stop-reasons
22. https://ai.google.dev/gemini-api/docs/function-calling
23. https://ai.google.dev/gemini-api/docs/tools
24. https://ai.google.dev/gemini-api/docs/computer-use
25. https://ai.google.dev/gemini-api/docs/generate-content/code-execution
26. https://ai.google.dev/gemini-api/docs/pricing
27. https://ai.google.dev/gemini-api/docs/billing.md.txt
28. https://docs.cloud.google.com/gemini-enterprise-agent-platform/reference/models/function-calling
29. https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/tools/function-calling
30. https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/computer-use
31. https://blog.google/innovation-and-ai/models-and-research/google-deepmind/gemini-computer-use-model/
32. https://github.com/google-gemini/computer-use-preview
33. https://docs.aws.amazon.com/bedrock/latest/userguide/tool-use.html
34. https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html
35. https://aws-samples.github.io/amazon-bedrock-samples/agents-and-function-calling/function-calling/function_calling_with_converse/function_calling_with_converse/
36. https://aws.amazon.com/blogs/aws/run-isolated-sandboxes-with-full-lifecycle-control-aws-lambda-introduces-microvms/
37. https://aws.amazon.com/about-aws/whats-new/2026/06/aws-lambda-microvms/
38. https://www.infoq.com/news/2026/06/aws-lambda-microvms/
39. https://modelcontextprotocol.org/specification/2025-11-25/basic/authorization
40. https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/security_best_practices
41. https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/security-considerations
42. https://auth0.com/blog/mcp-specs-update-all-about-auth/
43. https://playwright.dev/docs/browser-contexts
44. https://playwright.dev/mcp/introduction
45. https://playwright.dev/mcp/snapshots
46. https://playwright.dev/mcp/configuration/options
47. https://github.com/microsoft/playwright-mcp
48. https://github.com/browser-use/browser-use
49. https://docs.browser-use.com/open-source/examples/templates/playwright-integration
50. https://e2b.dev/docs/faq/calculate-sandbox-price
51. https://e2b.dev/docs/billing
52. https://www.e2b.dev/pricing
53. https://e2b.dev/
54. https://github.com/firecracker-microvm/firecracker/blob/448df604b0ff8c3c9e7e98cb7808dd51c25a1d58/SPECIFICATION.md
55. https://firecracker-microvm.github.io/
56. https://numavm.com/blog/2026-03-10-1-second-boot/
57. https://gvisor.dev
58. https://northflank.com/blog/what-is-gvisor
59. https://temporal.io/blog/announcing-openai-agents-sdk-integration
60. https://temporal.io/blog/introducing-temporal-and-agentic-sandboxes-openai-agents-sdk
61. https://go.temporal.io/platform-hub/ai-engineering/ai-reference-architecture
62. https://docs.stripe.com/api/idempotent_requests
63. https://datatracker.ietf.org/doc/html/draft-ietf-httpapi-idempotency-key-header
64. https://www.rfc-editor.org/rfc/rfc8288.html
65. https://relay.dev/graphql/connections.htm
66. https://owasp.org/www-community/attacks/MCP_Tool_Poisoning
67. https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html
68. https://labs.cloudsecurityalliance.org/research/csa-research-note-indirect-prompt-injection-in-the-wild-2026/
69. https://github.com/modelcontextprotocol/servers/issues/3660
70. https://gorilla.cs.berkeley.edu/leaderboard
71. https://gorilla.cs.berkeley.edu/blogs/15_bfcl_v4_web_search.html
72. https://proceedings.mlr.press/v267/patil25a.html
73. https://google.github.io/adk-docs/tools-custom/function-tools/
74. https://google.github.io/adk-docs/tools-custom/
75. https://docs.crewai.com/en/learn/create-custom-tools
76. https://docs.crewai.com/edge/en/concepts/crews
77. https://community.openai.com/t/should-auto-spawn-a-new-container-after-timeout/1291625
78. https://sujeet.pro/articles/stripe-idempotency-reliability
79. https://www.e2b.dev/blog/customize-sandbox-compute
80. https://arxiv.org/html/2603.21642v1
