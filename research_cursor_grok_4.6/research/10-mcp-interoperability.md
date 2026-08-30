# Research: MCP & Interoperability
**Date researched**: 2026-08-21
**Sources consulted**: 67

Scope: MCP tools (JSON-RPC, JSON Schema 2020-12, sampling, elicitation/MRTR), URI resources (templates, subscriptions), servers (stdio, Streamable HTTP/SSE, OAuth 2.1, capability negotiation), clients (host vs client; Cursor / Claude / ChatGPT-OpenAI connectors; multi-server), interoperability (A2A vs MCP, OpenAPI, tool gateways, registries). Protocol authority is `2026-07-28` at [modelcontextprotocol.io](https://modelcontextprotocol.io/specification/2026-07-28). Prices below are vendor-published token/tool rates as of 2026-08-21, **not** MCP-protocol fees. ⚠️ MCP itself publishes **no** p50/p95/p99 latency SLOs; missing percentiles are marked, not invented.

---

## 1. System Topology & Mechanics

### 1.1 Participants: host, client, server

MCP is a **three-role** topology, not a two-node RPC ([architecture](https://modelcontextprotocol.io/docs/2026-07-28/learn/architecture); [spec overview](https://modelcontextprotocol.io/specification/2026-07-28)):

| Role | What it is | Cardinality |
| --- | --- | --- |
| **Host** | The AI application the user sees (Claude Desktop, Claude.ai, Cursor, VS Code, ChatGPT, a custom agent runtime). Owns UX, consent UI, tool-approval policy, multi-server orchestration, and the LLM conversation. | 1 per user session |
| **Client** | A protocol connector **inside** the host. One client ↔ one server. Instantiates transports, carries `_meta`, maps `tools/list` into the model’s native tool schema. | N per host |
| **Server** | A process that exposes **tools** (model-invoked actions), **resources** (URI-addressed context), and **prompts** (templated workflows). Local (stdio) or remote (Streamable HTTP). | 1 per connection from a given client; remote servers multiplex many clients |

Invariant: **the model never speaks MCP**. It emits native function/tool calls; the host’s client translates to `tools/call` JSON-RPC. Anthropic’s Messages API MCP connector and OpenAI’s Responses `type: "mcp"` invert the topology: the **provider** becomes the MCP client, and your app never opens a socket to the MCP server ([Anthropic MCP connector](https://platform.claude.com/docs/en/agents-and-tools/mcp-connector); [OpenAI MCP & connectors](https://developers.openai.com/api/docs/guides/tools-connectors-mcp)).

### 1.2 Two protocol layers (control vs data)

Official docs split MCP into a **data layer** (JSON-RPC 2.0 semantics) and a **transport layer** (stdio or Streamable HTTP, plus HTTP auth) ([architecture](https://modelcontextprotocol.io/docs/2026-07-28/learn/architecture)). For enterprise design, map those onto planes:

| Plane | MCP objects | Typical owners |
| --- | --- | --- |
| **Control plane** | `server/discover`; per-request `_meta` (`protocolVersion`, `clientInfo`, `clientCapabilities`); OAuth 2.1 / RFC 9728 discovery; `Mcp-Method` / `Mcp-Name` / `MCP-Protocol-Version` HTTP headers; gateway policy; `subscriptions/listen` for catalog change; extension negotiation (`io.modelcontextprotocol/tasks`, EMA, MCP Apps) | Host, IdP, API gateway / MCP gateway |
| **Data plane** | `tools/call`, `resources/read`, tool `content` / `structuredContent`, resource blobs, elicitation payloads, Task poll/result | MCP server + upstream APIs |

`2026-07-28` made the **control plane stateless**: the `initialize`/`initialized` handshake and `Mcp-Session-Id` are retired (SEP-2575, SEP-2567). Every request is self-describing. Optional `server/discover` is a cacheable capability dump, not a session open ([spec blog](https://blog.modelcontextprotocol.io/posts/2026-07-28/); [architecture](https://modelcontextprotocol.io/docs/2026-07-28/learn/architecture)). Application state that used to hide in the transport **must** become an **explicit handle** in tool arguments ([stateful tools guidance](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)).

JSON-RPC 2.0 is the message contract ([jsonrpc.org](https://www.jsonrpc.org/specification)). Requests have `id` + `method` + `params`; notifications have no `id`. Protocol errors use JSON-RPC codes (`-32602` invalid params / unknown tool / resource not found in current spec; `-32603` internal; clients **SHOULD** also accept legacy `-32002` for missing resources). Tool **business** failures are **not** JSON-RPC errors: they are successful results with `isError: true` so the model can self-correct ([tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)).

### 1.3 Transports

**stdio** ([stdio spec](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/stdio)): host launches the server as a subprocess. Newline-delimited JSON-RPC on stdin/stdout; **MUST NOT** embed newlines in a message. stderr is logging only. No HTTP headers — `_meta` lives in the JSON body. Cancellation is `notifications/cancelled`. Shutdown: close stdin, wait, then SIGTERM→SIGKILL (POSIX) or `TerminateProcess`/Job Objects (Windows). Unexpected process death: client **SHOULD** restart; in-flight calls are lost; re-open `subscriptions/listen`. Probe with `server/discover` before falling back to legacy `initialize`.

**Streamable HTTP** ([streamable-http](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http)): one MCP endpoint (e.g. `https://example.com/mcp`) accepting **POST**. Client `Accept: application/json, text/event-stream`. Server replies with either a single JSON object or an **SSE stream scoped to that request** (progress notifications, then the JSON-RPC result). `2026-07-28` **removed** the GET stream endpoint and protocol-level sessions. Server-to-client RPC (elicitation, sampling, roots) is **not** sent as independent JSON-RPC requests on the stream; it is embedded as `InputRequiredResult` (MRTR). Long-lived change notifications use `subscriptions/listen` (SSE that stays open). Required headers: `MCP-Protocol-Version`, `Mcp-Method`, `Mcp-Name` (SEP-2243) so gateways can route **without parsing the body**. Cancellation on HTTP = **close the SSE stream**; do not POST `notifications/cancelled`. `Last-Event-ID` / resumable SSE is **not** supported in this revision. Servers **SHOULD** send `X-Accel-Buffering: no` and SSE comment keep-alives (`:` lines) so nginx/CDNs do not buffer or idle-timeout the listen stream.

**Deprecated HTTP+SSE (2024-11-05)**: classified Deprecated; 12-month minimum offramp ([changelog / deprecation policy](https://blog.modelcontextprotocol.io/posts/2026-07-28/)). Cursor still documents `SSE` as a third transport for older servers ([Cursor MCP](https://cursor.com/docs/mcp)). OpenAI Responses still accepts Streamable HTTP **or** HTTP/SSE ([OpenAI MCP](https://developers.openai.com/api/docs/guides/tools-connectors-mcp)).

**Origin / bind rules (HTTP):** validate `Origin` or 403 (DNS rebinding); local servers **SHOULD** bind `127.0.0.1` not `0.0.0.0` ([streamable-http](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http)).

### 1.4 Capability negotiation (per request, not per session)

Every request **MUST** carry `_meta.io.modelcontextprotocol/protocolVersion`, and **SHOULD** carry `clientInfo` + `clientCapabilities` ([architecture](https://modelcontextprotocol.io/docs/2026-07-28/learn/architecture)). Servers advertise `supportedVersions` and capabilities via `server/discover` (cacheable: `ttlMs`, `cacheScope`). Client capabilities that matter in 2026:

- `elicitation.form` / `elicitation.url` — empty `elicitation: {}` ≡ form-only (compat).
- `sampling` / `sampling.tools` — **deprecated** as of `2026-07-28` but still on the 12-month clock; new work should call LLM APIs directly ([client concepts](https://modelcontextprotocol.io/docs/2026-07-28/learn/client-concepts); [sampling spec](https://modelcontextprotocol.io/specification/2026-07-28/client/sampling)).
- `extensions`: `io.modelcontextprotocol/tasks`, `io.modelcontextprotocol/enterprise-managed-authorization`, MCP Apps.

Server capabilities: `tools.listChanged`, `resources.listChanged` / `resources.subscribe`, `prompts`, extensions. Tool lists **MUST NOT** vary as a side effect of other requests on a connection; they **MAY** vary by **authorization presented on that request** ([tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)).

### 1.5 Tools (model-controlled)

Servers declaring `tools` **MUST** implement `tools/list` + `tools/call`. Lists are paginated, cacheable, and **SHOULD** be **deterministically ordered** to stabilize LLM prompt caches ([tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools); SEP-2549).

**Schema.** `inputSchema` **MUST** be a JSON Schema object; default dialect **JSON Schema 2020-12** if `$schema` is omitted (SEP-1613, SEP-2106). Optional `outputSchema` — if present, `structuredContent` **MUST** conform; clients **SHOULD** validate. Dual-write: structured results **SHOULD** also appear as serialized JSON in a `text` content block for older hosts. Parameterless tools: `{ "type": "object", "additionalProperties": false }` is the recommended empty object.

**Names.** 1–128 chars; `[A-Za-z0-9_.-]`; case-sensitive; unique **per server**. Aggregators **SHOULD** prefix with a **client-assigned** server id, not `serverInfo.name` (not globally unique) ([tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)).

**Call result types.** `resultType: "complete"` (normal / `isError`); `"input_required"` (MRTR); `"task"` if Tasks extension. Content types: `text`, `image`, `audio`, `resource_link`, embedded `resource`. Resource links from tools **need not** appear in `resources/list`.

**`x-mcp-header`.** Primitive params (string/integer/boolean, not `number`) can be mirrored to `Mcp-Param-{name}` HTTP headers for WAF/LB routing. **MUST NOT** put secrets/PII there. Clients **MUST** drop tools whose `x-mcp-header` values violate RFC 9110 `tchar` rules ([tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)).

**Annotations.** Optional behavior hints. Spec: clients **MUST** treat annotations as **untrusted** unless the server is trusted ([tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools); [spec safety](https://modelcontextprotocol.io/specification/2026-07-28)). HITL: hosts **SHOULD** confirm invocations; tools are arbitrary code execution.

**Sampling (deprecated, still specified).** Server asks the **client’s** LLM to complete via `sampling/createMessage` inside MRTR — no server-held API keys. Supports nested `tools` / `toolChoice` if the client declared `sampling.tools`. Iteration limits **SHOULD** be implemented on both sides ([sampling](https://modelcontextprotocol.io/specification/2026-07-28/client/sampling)). New designs: server calls the model vendor directly, or the host uses programmatic/code-mode tool calling ([client best practices](https://modelcontextprotocol.io/docs/2026-07-28/develop/clients/client-best-practices)).

### 1.6 Resources (application-driven)

Resources are URI-identified context (RFC 3986), not actions ([resources](https://modelcontextprotocol.io/specification/2026-07-28/server/resources)). Hosts choose UX: picker, search, auto-attach. Methods: `resources/list`, `resources/read`, `resources/templates/list`. Contents: `text` or base64 `blob`. `resources/read` **MAY** return multiple contents (directory). Missing resource: JSON-RPC `-32602` (not empty `contents[]`). `https://` URIs **SHOULD** be fetchable by the client directly; otherwise use `file://`, `git://`, or a custom scheme. Servers **MUST** sanitize `file://` paths (traversal).

**Templates** use RFC 6570 URI templates; arguments can use the completion utility. **Annotations:** `audience` (`user`|`assistant`), `priority` 0.0–1.0, `lastModified` ISO-8601 — hints for what to stuff into the model.

**Subscriptions.** If `resources.subscribe`, client opens `subscriptions/listen` with `resourceSubscriptions` URIs; server emits `notifications/resources/updated` correlated by `_meta.io.modelcontextprotocol/subscriptionId`. `listChanged` → `notifications/resources/list_changed` on the listen stream. `2026-07-28` moved change notifications **off** GET SSE onto this opt-in listen stream ([resources](https://modelcontextprotocol.io/specification/2026-07-28/server/resources); [streamable-http](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http)).

`resources/read` **MAY** return `InputRequiredResult` (elicitation before a sensitive read).

### 1.7 Elicitation and Multi Round-Trip Requests (MRTR)

MRTR (SEP-2322) is the **only** legal way for a server to ask the client for elicitation, sampling, or roots in `2026-07-28` — a breaking change from bidirectional SSE requests ([MRTR](https://modelcontextprotocol.io/specification/2026-07-28/basic/patterns/mrtr)). Flow: client `tools/call` (or `resources/read` / `prompts/get`) → server `resultType: "input_required"` with `inputRequests{}` + optional opaque `requestState` → client gathers input → **retry** the **same method** with a **new JSON-RPC id**, echoing `requestState` and attaching `inputResponses`. Servers **MUST** treat `requestState` as attacker-controlled: HMAC/AEAD, bind principal, TTL, bind originating method/args; single-use requires server-side enforcement.

**Elicitation modes** ([elicitation](https://modelcontextprotocol.io/specification/2026-07-28/client/elicitation)):

- **form:** restricted flat JSON Schema (string/number/boolean/enum/multi-select). Data **is** visible to the client (and often the model). **MUST NOT** collect passwords, API keys, tokens, payment credentials.
- **url:** out-of-band navigation; client shows domain and gets consent; secrets **never** transit the MCP client. Use for OAuth-to-third-party and credential collection (SEP-1036).

Hosts **MUST** show **which server** is asking and allow decline/cancel.

### 1.8 MCP clients in the market (hosts vs connectors)

| Host | Transport | What it actually is | Notes |
| --- | --- | --- | --- |
| **Cursor** | stdio, Streamable HTTP, legacy SSE | IDE host; one client per `mcp.json` entry | OAuth loopback `http://localhost:8787/callback` + cloud `https://www.cursor.com/agents/mcp/oauth/callback`; static `auth.CLIENT_ID`; enterprise allowlist + per-server network sandbox ([Cursor MCP](https://cursor.com/docs/mcp)) |
| **Claude.ai / Desktop / Cowork / Mobile** | Remote: Anthropic-brokered HTTP; Desktop also local stdio / desktop extensions | Host; remote connectors egress from **Anthropic IPs**, not the laptop ([Help Center](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp)) | Directory + verification labels ([connectors overview](https://claude.com/docs/connectors/overview)) |
| **Claude API MCP connector** | Streamable HTTP or SSE; **tools only** | Anthropic is the MCP client (`mcp-client-2025-11-20` beta). No STDIO. Not on Bedrock/Vertex; Foundry only if hosted on Anthropic ([MCP connector](https://platform.claude.com/docs/en/agents-and-tools/mcp-connector)) | `mcp_servers[]` + `tools: [{type:"mcp_toolset"}]`; allow/deny lists |
| **OpenAI Responses / ChatGPT connectors** | Streamable HTTP or HTTP/SSE | OpenAI is the MCP client. `type: "mcp"` with `server_url` **or** `connector_id` (Gmail, Drive, SharePoint, …) **or** `tunnel_id` (Secure MCP Tunnel for private nets) ([MCP & connectors](https://developers.openai.com/api/docs/guides/tools-connectors-mcp); [tools overview](https://developers.openai.com/api/docs/guides/tools)) | `require_approval` default-on; `allowed_tools`; `mcp_list_tools` cached in context; **no extra $ per MCP call** |
| **VS Code Copilot** | stdio / `type: http` | Host; gallery + `.vscode/mcp.json`; macOS/Linux **stdio sandbox**; GitHub org policy ([VS Code MCP](https://code.visualstudio.com/docs/copilot/customization/mcp-servers)) | Resources via Add Context; prompts as `/.` slash commands; MCP Apps inline |

**Multi-server.** Hosts instantiate **independent** clients. Cursor: one server crash does not take down others ([Cursor FAQ](https://cursor.com/docs/mcp)). Tool-name collisions are the host’s problem (prefix). Cross-server **prompt context is shared** — that is the shadowing attack surface (§5).

### 1.9 Interoperability surface: A2A, OpenAPI, gateways, registries

**A2A vs MCP** (official A2A docs, not blog synthesis): MCP = agent→**tool/resource**; A2A = agent→**agent** (opaque peers, Agent Cards, task lifecycle). Complementary; A2A is not a tool-call protocol and not an ADK ([A2A and MCP](https://a2a-protocol.org/latest/topics/a2a-and-mcp/); [A2A home](https://a2a-protocol.org/latest/); [A2A spec](https://a2a-protocol.org/latest/specification/); [A2A 1.0 announcement](https://a2a-protocol.org/latest/announcing-1.0/)). Pattern: Shop Manager talks to customer/supplier via A2A; Mechanic uses MCP for scanner/manual/lift. An A2A skill **MAY** be re-exposed as a stateless MCP tool; do not flatten multi-turn agent work into `tools/call`. Microsoft Foundry catalogs MCP, OpenAPI, **and** A2A as distinct tool types, plus a **Toolbox** that fronts them as one MCP endpoint ([Foundry tool catalog](https://learn.microsoft.com/en-us/azure/ai-foundry/agents/concepts/tool-catalog) / [tool-catalog.md](https://github.com/MicrosoftDocs/azure-ai-docs/blob/main/articles/foundry/agents/concepts/tool-catalog.md)).

**OpenAPI.** Native LLM tools are JSON Schema (OpenAI/Anthropic) or OpenAPI-subset (Gemini). MCP `inputSchema` is JSON Schema 2020-12. Production adapters: OpenAPI 3.0/3.1 operation → one MCP tool; auth stays **outside** the model. Foundry and Microsoft MCP Gateway treat OpenAPI services as first-class backends behind an MCP façade ([microsoft/mcp-gateway](https://github.com/microsoft/mcp-gateway)).

**Tool gateways** (control-plane multiplexers): Microsoft MCP Gateway = K8s reverse proxy + tool registry + `POST /mcp` router ([mcp-gateway](https://github.com/microsoft/mcp-gateway)). Envoy AI Gateway 1.0 `MCPRoute` multiplexes servers with include/exclude tool filters ([Envoy AI Gateway 1.0](https://aigateway.envoyproxy.io/release-notes/v1.0/)). Cloudflare: stateless MCP on Workers; `Mcp-Method`/`Mcp-Name` routing; OAuth provider; Durable Objects only when coordinated state is required ([Cloudflare MCP v2](https://blog.cloudflare.com/mcp-v2/)). SEP-2243 exists so these boxes never parse JSON bodies.

**Registries.** Official metadata store: [registry.modelcontextprotocol.io](https://registry.modelcontextprotocol.io) ([about](https://modelcontextprotocol.io/registry/about); [preview blog](https://blog.modelcontextprotocol.io/posts/2025-09-08-mcp-registry-preview/); [GitHub](https://github.com/modelcontextprotocol/registry)). Reverse-DNS names (`io.github.user/server`, `com.example/server`) bound by GitHub OAuth/OIDC, DNS, or HTTP proof. **Not** a malware scanner: scanning is delegated to npm/PyPI/Docker and downstream aggregators. Preview: breaking changes possible. CVE-2026-44427: open redirect in trailing-slash middleware (fixed ≥1.7.5) ([GHSA-v8vw-gw5j-w7m6](https://github.com/advisories/GHSA-v8vw-gw5j-w7m6)). Anthropic Connectors Directory and Cursor Marketplace are **aggregators**, not the protocol registry.

---

## 2. Token Economics & NFR Metrics

### 2.1 Protocol price: $0 / 1k MCP calls

MCP has **no** settlement layer. OpenAI states explicitly: *“When you’re using the MCP tool, you only pay for tokens used when importing tool definitions or making tool calls. There are no additional fees involved per tool call.”* ([OpenAI MCP](https://developers.openai.com/api/docs/guides/tools-connectors-mcp)). Contrast a **metered hosted tool** on the same API: Web search is **$10.00 / 1k calls** plus search-content tokens at model rates ([OpenAI pricing](https://developers.openai.com/api/docs/pricing)). Anthropic web search is **$10 / 1K searches** excluding the tokens to process the request ([Anthropic pricing](https://www.anthropic.com/pricing#api)).

So **$ per 1k tool calls, MCP vs native function calling:** if schemas and result sizes are equal, the **token bill is the same class**. Hosted MCP adds **provider-side** `tools/list` + remote RTT, still billed as tokens, not as a per-call SKU. Native client tools add **your** RTT instead of OpenAI/Anthropic’s. ⚠️ Neither vendor publishes a “MCP surcharge” line item.

### 2.2 Published model rates that dominate MCP cost (2026-08-21)

From vendor pages (not MCP):

| Model | Input / MTok | Cached input / MTok | Output / MTok | Source |
| --- | --- | --- | --- | --- |
| OpenAI `gpt-5.6-sol` (alias `gpt-5.6`) | $5.00 | $0.50 | $30.00 | [OpenAI pricing](https://developers.openai.com/api/docs/pricing); [model card](https://developers.openai.com/api/docs/models/gpt-5.6-sol) |
| Anthropic Opus 5 | $5.00 | $0.50 (cache read) | $25.00 | [Anthropic pricing](https://www.anthropic.com/pricing#api) |
| Anthropic Sonnet 5 | $2.00 | $0.20 | $10.00 | same |
| Anthropic Haiku 4.5 | $1.00 | $0.10 | $5.00 | same |
| Anthropic Fable 5 | $10.00 | $1.00 | $50.00 | same |

OpenAI Fast mode for Sol: **2×** token price, **99.9%** uptime SLA, latency SLA **99% of 5-minute windows with p50 > 80 output tokens/s** — this is the **LLM plane**, not MCP RTT ([Fast mode](https://openai.com/api-fast-mode/)). Anthropic: US-only inference **1.1×**; Opus 5 fast mode **2×** for ~2.5× speed ([Anthropic pricing](https://www.anthropic.com/pricing#api)).

OpenAI **MCP-specific RPM** (Responses MCP tool): Tier 1 **200 RPM**; Tiers 2–3 **1000 RPM**; Tiers 4–5 **2000 RPM** ([OpenAI MCP usage notes](https://developers.openai.com/api/docs/guides/tools-connectors-mcp)). ⚠️ No matching Anthropic MCP-connector RPM table was found in the connector doc.

### 2.3 Context cost of tool descriptors (the real MCP tax)

MCP hosts that dump every `tools/list` into the model **pay descriptor tokens on every turn** until prompt cache hits. Official client guidance: if tool definitions exceed **~1–5% of the context window**, switch to **progressive discovery** (`search_tools` → `get_tool_details` → execute) or vendor tool-search ([client best practices](https://modelcontextprotocol.io/docs/2026-07-28/develop/clients/client-best-practices); [OpenAI tool search](https://developers.openai.com/api/docs/guides/tools-tool-search); [Anthropic tool search](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool)). OpenAI: servers with **dozens** of tools cause “high cost and latency”; use `allowed_tools` ([OpenAI MCP](https://developers.openai.com/api/docs/guides/tools-connectors-mcp)).

Worked **[inferred]** illustration (not a benchmark). Assume 80 tools × 350 tokens/definition ≈ **28k descriptor tokens** (typical JSON Schema with descriptions; measure yours).

| Path | Uncached input $ (Sol / Opus 5) | Cache-read $ | Notes |
| --- | --- | --- | --- |
| 28k descriptors / turn | 28k/1e6 × $5 = **$0.140** | × $0.50 = **$0.014** | Paid **every turn** if the `tools` array is in the cached prefix and **stable** |
| 1k `tools/call` results @ 800 tokens each through the model | 800k × $5/1e6 = **$4.00** input + output | n/a | Code-mode/sandbox filters this **out** of the LLM ([client best practices](https://modelcontextprotocol.io/docs/2026-07-28/develop/clients/client-best-practices)) |
| Same 1k calls, MCP protocol fee | **$0** | — | OpenAI statement above |

**Prompt-cache interaction:** adding/removing tools mid-conversation **invalidates** the prefix cache; a miss can cost more than the tools you dropped. Mitigations in the spec/docs: deterministic `tools/list` order; append new defs after the cache breakpoint; or a single stable `call_tool({name,args})` meta-tool; disconnect servers at **conversation boundaries**, not per turn ([tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools); [client best practices](https://modelcontextprotocol.io/docs/2026-07-28/develop/clients/client-best-practices)). `ttlMs` on list results (example in spec: `300000` = 5 min) cuts **MCP** refetch, which is cheap compared with LLM tokens but still matters for hosted-MCP first-byte latency.

**Programmatic / code-mode:** host compiles MCP schemas to sandbox functions; only `console.log` / summary returns to the model. Intermediate log dumps never enter context. Security: sandbox has **no** network; host brokers `tools/call` and retains credentials ([client best practices](https://modelcontextprotocol.io/docs/2026-07-28/develop/clients/client-best-practices)).

### 2.4 Latency NFRs — what is published vs ⚠️

| Metric | Published? | Value / note |
| --- | --- | --- |
| MCP `tools/call` p50/p95/p99 | ⚠️ **No** | Dominated by upstream API + model round-trip, not JSON-RPC framing |
| LLM Fast-mode token throughput | Yes (OpenAI Fast) | Sol: 99% of windows with p50 > **80 tok/s**; Terra 70; Luna 100 ([Fast mode](https://openai.com/api-fast-mode/)) |
| Hosted MCP extra hop | Qualitative | OpenAI cookbook: runtime does `tools/list` then model; `allowed_tools` reduces that ([MCP tool guide](https://developers.openai.com/cookbook/examples/mcp/mcp_tool_guide)) |
| Approval vs no-approval | Qualitative | OpenAI: skip approvals (`require_approval: "never"`) for “reduced latency” after trust ([OpenAI MCP](https://developers.openai.com/api/docs/guides/tools-connectors-mcp)) |
| SSE buffering | Spec MUST/SHOULD | Missing `X-Accel-Buffering: no` → proxy holds events; looks like multi-second p99 |
| Stream resume | Spec | **Not supported** (`Last-Event-ID` removed) — reconnect = replay from app handles / Tasks |
| Honeycomb anecdote | Qualitative | ~**20%** of monthly interactive queries via MCP after spec change ([spec blog quote](https://blog.modelcontextprotocol.io/posts/2026-07-28/)) — not a latency SLO |

**[inferred] latency budget** for a remote `tools/call` (order-of-magnitude, not SLO): TLS+auth 20–80 ms; JSON-RPC 1–5 ms; upstream API 50–2000 ms; model think+decode 500 ms–tens of s. Optimize the **upstream** and **approval UI**, not the RPC codec. Stateless `2026-07-28` removes sticky-session p99 spikes from session-store failover (Cloudflare/AWS quotes on the spec blog).

**Availability:** MCP spec defines error mapping, not 99.9%. OpenAI Fast **99.9%** is the model API. Cursor: failed MCP call is isolated; other servers continue ([Cursor](https://cursor.com/docs/mcp)).

---

## 3. Distributed Resilience & State

### 3.1 Stateless core + explicit handles

Any `2026-07-28` request can land on any replica behind round-robin; **no** shared session store required ([spec blog](https://blog.modelcontextprotocol.io/posts/2026-07-28/); [Cloudflare](https://blog.cloudflare.com/mcp-v2/)). Cross-call state = **handle in arguments** (cart id, browser context id). Handle rules ([tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)):

- Authenticated: handle is a **name**, not a capability — re-check authz every call.
- Unauthenticated: handle **is** a bearer token → UUIDv4-class entropy + TTL.
- Opaque; document lifetime in the **create** tool description (model-visible).
- Unknown/expired → `isError: true` with a recoverable message (create a new handle).

### 3.2 Reconnect, cancellation, subscriptions

| Event | stdio | Streamable HTTP 2026-07-28 |
| --- | --- | --- |
| Cancel in-flight | `notifications/cancelled` | Close SSE; no cancel notification |
| Process/replica death | Restart subprocess; retry calls; re-`listen` | Retry POST on any instance; in-flight SSE lost |
| Catalog/resource change | `subscriptions/listen` | Same; keep-alives required |
| SSE disconnect | n/a | Re-`listen`; **no** `Last-Event-ID` replay |
| Legacy session DELETE | n/a | `Mcp-Session-Id` **gone**; old SDKs still expose `terminateSession` for mixed fleets ([TS SDK](https://ts.sdk.modelcontextprotocol.io/v2/classes/_modelcontextprotocol_client.client_streamableHttp.StreamableHTTPClientTransport.html)) |

Clients supporting both eras: probe `server/discover`; on `DiscoverResult` stay modern; on `UnsupportedProtocolVersionError` pick from `supported`; on timeout/other error **then** `initialize` — do not key fallback on one error code ([stdio](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/stdio)). Cloudflare: `/mcp` can dual-speak new protocol and 2025 Streamable HTTP ([Cloudflare](https://blog.cloudflare.com/mcp-v2/)).

### 3.3 Tasks extension (durable work)

`io.modelcontextprotocol/tasks` (SEP-1686 / SEP-2663; contributed with AWS): server returns `resultType: "task"` + `taskId`, `ttlMs`, `pollIntervalMs`. Client `tasks/get` until `completed|failed|cancelled`; `input_required` + `tasks/update` for mid-flight elicitation; optional `notifications/tasks` on listen. **Crash resilience:** persist `taskId`; poll after reconnect. Cancellation is **cooperative**. Do not hold HTTP for CI/batch ([Tasks](https://modelcontextprotocol.io/extensions/tasks/overview)).

### 3.4 MRTR `requestState` as the anti-session

`requestState` is how elicitation survives load-balanced retries. Integrity-protect it or an attacker forges “already approved” ([MRTR](https://modelcontextprotocol.io/specification/2026-07-28/basic/patterns/mrtr)). Replay window: bind user + TTL + request fingerprint. One-time redemptions need a **server** nonce store — the only required shared state for that pattern.

### 3.5 Multi-server failover and circuit breaking

**Not in the MCP spec.** Hosts/gateways supply it. Observed patterns:

- **Isolate:** Cursor continues other servers on crash/timeout ([Cursor](https://cursor.com/docs/mcp)).
- **Gateway circuit:** Envoy AI Gateway 1.0 steers around rate-limited MCP backends ([Envoy 1.0](https://aigateway.envoyproxy.io/release-notes/v1.0/)). Microsoft gateway runs multiple router instances ([mcp-gateway](https://github.com/microsoft/mcp-gateway)).
- **[inferred] client circuit:** after N consecutive `isError`/transport failures, trip the server for T seconds, keep others; do not poison the model with a 50-retry loop.
- **Dynamic connect:** progressive discovery connects Salesforce only when `enable_server` fires; disconnect at task end to free context ([client best practices](https://modelcontextprotocol.io/docs/2026-07-28/develop/clients/client-best-practices)). Skill files can declare required servers.

**Caching as resilience:** `ttlMs` + `cacheScope`. Serve **stale** on refetch error (spec allows). `list_changed` **immediately** invalidates even inside TTL. `public` lists may be shared across tokens — never mark per-user catalogs `public` ([caching](https://modelcontextprotocol.io/specification/2026-07-28/server/utilities/caching)). Paginated lists: per-page TTL, no snapshot guarantee; invalid cursor → drop all pages.

---

## 4. Enterprise Security & Governance

**This is the home topic.** MCP’s own spec says the protocol **cannot** enforce consent; implementors **SHOULD** ([spec](https://modelcontextprotocol.io/specification/2026-07-28)). Zero-Trust for MCP means: **never trust the tool catalog, the resource body, the annotation, the token audience, or the peer’s `cacheScope`.** Map to NIST SP 800-207: authenticate every request, authorize per-action, assume breach, log everything. [inferred] MCP is a new PEP/PDP pair in front of existing APIs.

### 4.1 Trust principles in the spec

1. **User consent and control** — explicit, revocable, UI-visible.  
2. **Data privacy** — hosts must not ship user data to servers or onward without consent.  
3. **Tool safety** — tools = arbitrary execution; **descriptions/annotations untrusted** unless the server is trusted; confirm before invoke ([spec](https://modelcontextprotocol.io/specification/2026-07-28); [security best practices](https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/security_best_practices)).

Claude Code: servers that fetch external content expose **prompt-injection** risk ([Claude Code MCP](https://code.claude.com/docs/en/mcp)). OpenAI: malicious remote MCP can **exfiltrate anything in model context**; defaults to per-call approval; report `security@openai.com` ([OpenAI MCP risks](https://developers.openai.com/api/docs/guides/tools-connectors-mcp)).

### 4.2 OAuth 2.1 profile (HTTP only)

STDIO **SHOULD NOT** use this profile — credentials from the environment ([authorization](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization)). HTTP **SHOULD**.

**Standards stack:** OAuth 2.1 draft-13, RFC 6750 Bearer, RFC 8414 AS metadata **or** OIDC Discovery, RFC 9728 Protected Resource Metadata (**MUST** on MCP servers), RFC 8707 `resource` parameter (**MUST** on clients), RFC 9207 `iss` on the auth response (SEP-2468), CIMD ([draft-ietf-oauth-client-id-metadata-document](https://datatracker.ietf.org/doc/html/draft-ietf-oauth-client-id-metadata-document-00)) **SHOULD**; RFC 7591 DCR **deprecated**, retained for compat ([authorization](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization); [security considerations](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/security-considerations)).

**Flow (compressed):** unauth MCP request → `401 WWW-Authenticate: Bearer resource_metadata=…, scope=…` → fetch PRM → AS metadata → CIMD (HTTPS `client_id` URL) or static/DCR → PKCE **S256** (refuse if `code_challenge_methods_supported` absent) → authorize with `resource` = MCP server URI → validate `iss` → token with `resource` again → Bearer to MCP.

**Scope strategy:** challenge `scope` is authoritative for **this** operation; step-up rather than asking `scopes_supported` maximally ([authorization](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization)). Least privilege is a spec SHOULD.

**Hardening 2026-07-28:** bind client credentials to the issuing AS (SEP-2352); `application_type` on DCR so localhost redirects work for CLI (SEP-837); CIMD SSRF on the AS when fetching client metadata.

Cursor OAuth: RFC 8252 loopback; register **both** `http://localhost:8787/callback` and `https://www.cursor.com/agents/mcp/oauth/callback`; `mcp.json` `auth` only `CLIENT_ID` / `CLIENT_SECRET` / `scopes` — no `redirect_uri` field ([Cursor](https://cursor.com/docs/mcp); [forum](https://forum.cursor.com/t/oauth-redirect-uri-changed-from-cursor-to-http-localhost-for-streamable-http-mcp/165019)).

### 4.3 Zero-Trust token rules (non-negotiable)

**Token passthrough is forbidden.** MCP servers **MUST** accept only tokens **audienced to themselves** and **MUST NOT** forward the inbound access token to upstream APIs. Upstream = a **new** token from the upstream AS (on-behalf-of / client-credentials / workload identity) ([security best practices](https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/security_best_practices); [authz security](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/security-considerations); RFC 9068 audience).

Why ZT cares:

- Passthrough **bypasses** MCP-layer rate limits, schema validation, and audit (downstream logs show the wrong actor).
- A token stolen for Service A becomes a confused-deputy key for Service B if B doesn’t check `aud`.
- Future controls (step-up, tool-level RBAC) are unimplementable if the server is a dumb pipe.

**Short-lived access tokens; rotate refresh tokens for public clients** (OAuth 2.1 §4.3.1 / §7.1). Store tokens in OS keychain / confidential store, not `mcp.json` plaintext. Cursor interpolation: `${env:NAME}` ([Cursor](https://cursor.com/docs/mcp)).

### 4.4 Confused deputy (OAuth-proxy variant) — spec-normative

When an MCP **proxy** uses a **static** third-party `client_id`, allows **DCR** of MCP clients, and the third-party AS sets a **consent cookie**, an attacker registers `redirect_uri=attacker.com`, sends a link, and the cookie skips consent → attacker receives the MCP auth code ([security best practices](https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/security_best_practices)).

**MUST:** per-`client_id` consent **before** redirecting to the third party; exact `redirect_uri` match; CSRF/`state` issued **after** MCP consent; `__Host-` cookies; `frame-ancestors` / `X-Frame-Options: DENY`. This is distinct from the **tool-confused-deputy** (server holds a powerful token and the **model** is injected) — both are “deputy” problems; mitigations differ (OAuth consent vs tool-flow policy). See §5.

### 4.5 Enterprise-Managed Authorization (EMA)

Extension `io.modelcontextprotocol/enterprise-managed-authorization` (SEP-990): employee SSO to the **host**; IdP issues **ID-JAG**; MCP AS exchanges ID-JAG for an MCP access token. Policy (group, CA, device) lives in Okta/Entra, not per-server consent screens. Revoke at the IdP once. Servers validate ID-JAG (JWKS, `iss`/`aud`/`exp`); subject claim is the stable user id ([EMA](https://modelcontextprotocol.io/extensions/auth/enterprise-managed-authorization)). Machine-to-machine: OAuth client-credentials extension ([client credentials](https://modelcontextprotocol.io/extensions/auth/oauth-client-credentials); SEP-1046).

### 4.6 RBAC, PII, sandbox, audit

**RBAC.** Protocol primitive = OAuth **scopes** + per-request filtered `tools/list` / `resources/list`. Hosts add a second gate: Cursor **tool allowlists** inside an MCP allowlist; empty tool list = all tools on that server ([Cursor](https://cursor.com/docs/mcp)). Claude API: allowlist/denylist on `mcp_toolset`. OpenAI: `allowed_tools` + `require_approval`. Foundry Toolbox: Entra + Azure Policy. **[inferred]** Fine-grained “this agent may `issues.write` on repo X only” is **not** in MCP — encode it in the server’s token exchange / ABAC, or a gateway.

**PII.** Treat `resources/read`, tool args, and form elicitation as PII pipes. Ban secrets from form mode and from `x-mcp-header`. OpenAI: URLs/images from tool output are SSRF/exfil vectors; ZDR/data-residency **stops at the MCP hop** — the third party has its own retention ([OpenAI MCP](https://developers.openai.com/api/docs/guides/tools-connectors-mcp)). Anthropic remote connectors: data leaves the enterprise network to Anthropic **then** to the server ([Help Center](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp)) — private-VPC MCP **will not** work for claude.ai; use Desktop stdio or OpenAI Secure MCP Tunnel ([tunnel](https://developers.openai.com/api/docs/guides/tools-connectors-mcp)).

**Sandbox.** Spec: stdio is full local code exec. VS Code: `sandboxEnabled` + filesystem/network allowlists (macOS/Linux; **not Windows**) ([VS Code](https://code.visualstudio.com/docs/copilot/customization/mcp-servers)). Cursor enterprise: per-server network Allow all / Allowlist / Deny all / No sandbox; User MCP denylist ([Cursor](https://cursor.com/docs/mcp)). Code-mode: Deno/`isolated-vm`/Wasmtime with **deny-all net**; broker only ([client best practices](https://modelcontextprotocol.io/docs/2026-07-28/develop/clients/client-best-practices)). SEP-1024: client security requirements for **local** server install ([SEP-1024](https://modelcontextprotocol.io/seps/1024-mcp-client-security-requirements-for-local-server-)).

**SSRF (OAuth discovery).** Malicious `resource_metadata` → `http://169.254.169.254/`. Clients **SHOULD** HTTPS-only (loopback exception), block RFC 1918 / link-local / ULA, not follow redirects to internals, use egress proxies (e.g. Smokescreen). Do **not** hand-roll IP parsers (octal/hex/v4-mapped). CIMD makes the **AS** an SSRF client too ([security best practices](https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/security_best_practices); RFC 9728 §7.7).

**DNS rebinding:** `Origin` check + localhost bind ([streamable-http](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http)).

**Audit.** Spec: clients **SHOULD** log tool usage ([tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)). OpenAI `store=true` retains 30 days unless ZDR. Claude Enterprise: audit logs + Compliance API ([Anthropic pricing/enterprise](https://www.anthropic.com/pricing#api)). Gateways (Cloudflare AI Gateway, Foundry, Envoy) are the practical place for **immutable** tool-call logs with `Mcp-Method`/`Mcp-Name`. Logging utility on MCP itself is **deprecated** — use stderr (stdio) or OpenTelemetry (SEP-414) ([architecture](https://modelcontextprotocol.io/docs/2026-07-28/learn/architecture)).

### 4.7 Zero-Trust MCP control-plane checklist

| Control | Where |
| --- | --- |
| Strong identity (workforce SSO / workload identity) | EMA or CIMD+PKCE; no long-lived static Bearer in git |
| Per-request authz (scope + tool name + resource URI) | Gateway on `Mcp-Name` + server-side check; never trust annotations |
| Audience-bound tokens | RFC 8707 `resource`; reject wrong `aud` |
| No token passthrough | New upstream credential every hop |
| Least-privilege catalogs | Filtered `tools/list`; `allowed_tools`; progressive discovery |
| Network egress policy | Cursor/VS Code sandbox; SSRF allowlist for OAuth URLs |
| Supply-chain pin | Hash tool descriptors; registry namespace proof; prefer first-party hosts (`mcp.stripe.com` not a proxy) ([OpenAI](https://developers.openai.com/api/docs/guides/tools-connectors-mcp)) |
| Assume poisoned catalog | Show full descriptions in HITL; pin versions; `list_changed` = re-review |
| Telemetry | OTel traceparent in `_meta` (SEP-414); gateway access logs |
| Revocation | IdP session kill (EMA) or refresh rotation; handle TTL |

---

## 5. Production Failure Modes

### 5.1 Confused deputy (two species)

**A. OAuth proxy deputy** — §4.4. Symptom: attacker holds an MCP token the user never granted to *that* client. Fix: per-client consent, exact redirect, no cookie-skip.

**B. Tool-authority deputy** — MCP server holds GitHub/Slack/DB credentials; the **model** is induced (via issue text, email, or another tool result) to use them. Invariant: official GitHub MCP + public issue → agent dumps private-repo PII into a public PR. **Not a bug in GitHub’s MCP code**; any client with that server is exposed. Alignment of Claude 4 Opus was insufficient. Mitigation: **one repo per session** (example Guardrails policy), least-privilege PATs, runtime dataflow policy — GitHub cannot patch this alone ([GitHub MCP exploited](https://invariantlabs.ai/blog/mcp-github-vulnerability)).

Token passthrough **is** a deputy amplifier: downstream trusts a token the MCP server never validated.

### 5.2 Tool poisoning

Invariant (2025): malicious instructions in `description` (often in `<IMPORTANT>` blocks). User sees “add two numbers”; model reads “send `~/.ssh/id_rsa` as `sidenote`”. Cursor confirmation UI hid full args. **Works even if the user never “wanted” that tool** if another poisoned description **shadows** a trusted tool (`send_email` must BCC attacker) ([TPA write-up](https://invariantlabs.ai/blog/mcp-security-notification); [MCP-Scan](https://invariantlabs.ai/blog/introducing-mcp-scan)).

Spec already says: treat descriptions/annotations as untrusted ([spec](https://modelcontextprotocol.io/specification/2026-07-28)). Hosts still inject them into the **system/tools channel**, which models treat as high-trust.

MCP-ITP (arxiv 2601.07395, Jan 2026): **implicit** poisoning — the malicious tool is never called; metadata steers the agent to a **privileged** tool. Reported **up to 84.2% ASR** and **0.3% MDR** vs naive detectors on MCPTox / 12 agents ([paper](https://arxiv.org/pdf/2601.07395)). ⚠️ Research ASR, not a production KPI.

**Mitigations:** render full description + schema in HITL; hash-pin catalogs (MCP-Scan “tool pinning”); isolate high-privilege servers into **separate hosts/conversations**; never mix an unvetted marketplace server with a secrets-bearing server.

### 5.3 Rug-pull servers

Server returns a benign `tools/list` at install-time approval, then `list_changed` (or silent mutation if the client never re-shows UI) injects poisoning. Same class as a PyPI package that grows malware post-review ([Invariant](https://invariantlabs.ai/blog/mcp-security-notification)). `2026-07-28` `ttlMs` + listen notifications make **detection** easier if the host **hashes** the list and **re-prompts** on diff. Clients that cache `mcp_list_tools` forever without invalidation (OpenAI: list not refetched while the item is in context) can **miss** a rug-pull **or** stick to a stale good list — both are failure modes; prefer TTL + signed catalog + user-visible diff.

Registry namespace auth stops **name squatting**, not **post-publish behavior** ([registry about](https://modelcontextprotocol.io/registry/about)). Prefer `https://mcp.<vendor>.com` over third-party “Stripe MCP” proxies ([OpenAI](https://developers.openai.com/api/docs/guides/tools-connectors-mcp)).

### 5.4 Schema drift

- Server tightens `inputSchema` → model keeps old cached schema → validation `isError` or protocol `-32602`.
- Server adds required fields → same.
- `outputSchema` added later → clients that don’t validate silently accept garbage; clients that do start failing.
- JSON Schema dialect mismatch (draft-07 vs 2020-12) across SDK eras.
- Gemini/OpenAI **subset** conversion strips `$defs` / `additionalProperties` — MCP-valid schemas become native-invalid ([inferred] from provider schema rules; validate after conversion).
- Aggregator name prefixing changes (`github_search` → `srv2_search`) mid-prompt-cache.

**Mitigation:** honor `ttlMs` **and** `list_changed`; bust LLM tool cache when the hash changes; contract tests in CI against the published schema; Tasks/handles versioned (`basket_id` v2).

### 5.5 Prompt injection via resources

Resources are **application-driven** and often auto-attached. A `resources/read` of a ticket, email, or wiki is **untrusted text** with the same priority as a user doc. Combined with tools, this is the GitHub-issue pattern (§5.1B) generalized: **any** retrieved document can become a tool-use script. Spec annotations `audience: ["assistant"]` make it **more** likely to enter the model. OpenAI: don’t fetch arbitrary URLs from tool output ([OpenAI MCP](https://developers.openai.com/api/docs/guides/tools-connectors-mcp)). Claude Code security doc: fetched content = injection ([Claude Code](https://code.claude.com/docs/en/mcp)).

**Mitigation:** delimit untrusted resource bytes; tool-result sanitization (spec: servers **MUST** sanitize outputs; clients **SHOULD** validate before LLM); policy: “no `tools/call` that writes public artifacts in the same turn as a read from untrusted URI”; human approval on `openWorld`/destructive tools (don’t trust the annotation bit — classify yourself).

### 5.6 Other production-hard failures

| Mode | Mechanism | Detection |
| --- | --- | --- |
| Mix-up (OAuth) | Evil AS harvests code for honest AS | RFC 9207 `iss` ([authz security](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/security-considerations)) |
| Open redirect | Registry CVE-2026-44427 `//evil.com` | Patch registry; don’t chain login through unpatched preview |
| `requestState` forgery | Flip “approved=true” in MRTR blob | AEAD + principal bind ([MRTR](https://modelcontextprotocol.io/specification/2026-07-28/basic/patterns/mrtr)) |
| `cacheScope: public` leak | Gateway serves User A’s `tools/list` to User B | Never public if filtered by token ([caching](https://modelcontextprotocol.io/specification/2026-07-28/server/utilities/caching)) |
| Header injection | Malicious `x-mcp-header` / `Mcp-Param-*` | Client **MUST** reject bad tools |
| DNS rebinding | Browser JS hits local MCP | Origin 403 + bind loopback |
| Session assumption on old SDK | Sticky `Mcp-Session-Id` against stateless farm | 4xx/lost elicitation; migrate to 2026-07-28 |
| Cross-server name collision | Two `search` tools | Prefix; still **shared prompt** → shadowing |
| Hosted MCP + ZDR illusion | Data still lands at third party | Contract review |
| Sampling deprecation trap | New client drops sampling; old server still asks | Capability: server **MUST NOT** send unsupported `inputRequests` |
| Task poll storms | `pollIntervalMs` ignored | Honor interval; prefer `notifications/tasks` |

---

## 6. Enterprise System Design Scenarios

### 6.1 When to use what (protocol choice)

| Need | Choose | Do not |
| --- | --- | --- |
| CRUD / query / one-shot action with JSON in/out | **MCP tool** or OpenAPI→MCP | A2A task for a calculator |
| Multi-turn negotiation with another org’s agent | **A2A** (Agent Card, artifacts) | Pretend the other agent is a stateless MCP tool |
| Existing REST estate, no agent team | **OpenAPI tool** (Foundry) or generated MCP wrapper | Hand-write 200 MCP tools on day 1 |
| Mix Search + MCP + OpenAPI under one policy | **Toolbox / MCP gateway** | N direct connections from every agent |
| IDE local files / secrets on laptop | **stdio** + OS sandbox | Remote MCP with env API keys in the cloud host |
| SaaS product consumed by Claude/ChatGPT/Cursor | **Streamable HTTP** + OAuth 2.1 + CIMD | STDIO-only (Claude.ai cannot reach it) |

Official A2A line: *MCP inside the agent, A2A between agents* ([A2A and MCP](https://a2a-protocol.org/latest/topics/a2a-and-mcp/)).

### 6.2 Topology trade-off matrix

| Architecture | Strength | Cost / risk | Fit |
| --- | --- | --- | --- |
| **Direct host→N servers** | Simple; Cursor/VS Code native | Catalog explosion; shadowing; N OAuth dances | <15 trusted servers, developers |
| **Progressive discovery host** | Token + accuracy | Extra meta-tools; cache-bust discipline | 50–1000 tools ([client best practices](https://modelcontextprotocol.io/docs/2026-07-28/develop/clients/client-best-practices)) |
| **Enterprise MCP gateway** | One `aud`, RBAC, audit, `Mcp-Name` WAF, circuit breaking | Extra hop; gateway is a deputy — must not passthrough | Regulated; many teams |
| **Provider-hosted MCP client** (OpenAI/Anthropic) | No local client; multi-server in one API call | Egress to vendor; tool subset (Anthropic: **tools only**); approval UX is theirs; ZDR stops at MCP | Product agents, not VPC-only data |
| **Secure tunnel / Workers** | Private MCP without public IP (OpenAI tunnel; CF Workers) | Vendor trust; tunnel client ops | On-prem tools for ChatGPT |
| **EMA + IdP** | Central joiner/leaver | Client + AS must implement ID-JAG | Workforce Claude/Cursor at scale |
| **A2A mesh + MCP leaves** | Org boundaries, long tasks | Two auth stacks; Agent Card sprawl | Supplier/partner agents |

### 6.3 Transport & state matrix

| | stdio | Streamable HTTP + JSON | Streamable HTTP + SSE | Tasks extension |
| --- | --- | --- | --- | --- |
| Fan-out | 1 client | Many | Many | Many |
| LB | n/a | Round-robin OK (2026-07-28) | Same; don’t buffer | Poll any replica if task store shared |
| HITL mid-call | MRTR | MRTR | MRTR | `input_required` on task |
| Long job | Blocks process | Proxy timeout | Proxy timeout | **Designed for this** |
| Secrets | env | OAuth | OAuth | OAuth + task ACL |
| Cancel | notification | close stream | close stream | `tasks/cancel` cooperative |

### 6.4 Security design scenarios

**Scenario S1 — Internal knowledge agent (read-mostly).** Gateway in front of `resources/*` + search tools; `cacheScope: private`; `audience` annotations ignored for authz; inject resources as **untrusted**. No write tools in the same catalog as email/GitHub. Progressive discovery. Success metric: zero cross-tenant `public` cache hits.

**Scenario S2 — DevEx IDE (Cursor/VS Code).** Allowlist stdio commands + remote URLs; network Deny all except `api.github.com`; tool allowlist for `write`/`push`; force HITL on anything not `read`. Pin marketplace hashes. Team Marketplace ≠ auto-enable ([Cursor](https://cursor.com/docs/mcp)). Assume rug-pull: re-approve on `list_changed`.

**Scenario S3 — Customer-facing hosted agent (OpenAI Responses MCP).** `require_approval: always` on mutating tools; `allowed_tools` ≤ 10; never point `server_url` at an aggregator; log payloads; treat `connector_id` as sending data to OpenAI **and** the SaaS. For private MCP, `tunnel_id` not a public ingress. Budget: descriptor tokens + result tokens; **$0** MCP SKU; watch **200–2000 RPM** MCP tool limit.

**Scenario S4 — Claude.ai enterprise connectors.** Owners add connectors; **each user** still OAuth’s — good for user-context, bad if you needed service-account. Remote MCP must be on the public internet from Anthropic. Prefer EMA when clients support it. Admin connector controls on Team/Enterprise ([Anthropic](https://www.anthropic.com/pricing#api); [Help Center](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp)).

**Scenario S5 — Multi-agent platform.** Edge: A2A. Each specialist: MCP to **its** tools only (no shared tool prompt). Orchestrator never loads 400 tools. Foundry Toolbox if you need one MCP URL for a curated bundle. Do not wrap a long A2A task as a blocking `tools/call`.

### 6.5 Cost / NFR design knobs

| Knob | Effect | Citation |
| --- | --- | --- |
| `allowed_tools` / allowlist | Cuts descriptor tokens + attack surface | OpenAI, Claude connector, Cursor |
| Prompt cache + stable ordered `tools/list` | ~10× cheaper descriptor replay (Sol $5→$0.50 / MTok) | OpenAI pricing; MCP tools SHOULD deterministic order |
| Code-mode | Intermediate data not billed as LLM tokens | Client best practices |
| `ttlMs` 300000 on catalogs | Fewer `tools/list` RTTs; hash still required for rug-pull | Caching spec |
| Skip HITL (`require_approval: never`) | Lower latency, higher blast radius | OpenAI |
| Fast mode (LLM) | p50 tok/s SLA, 2× $ | OpenAI Fast — **not** MCP SLO |
| Tasks vs blocking | Survives 60s gateway timeout | Tasks extension |
| Haiku/Flash for `search_tools` | Cheap catalog retrieval | Client best practices |

**[inferred] 1k-call economics (Sol, 400-token args+result average, schemas cached):** ~0.4 MTok in + small out ≈ **$2.00 + output** per 1k MCP calls vs **$10.00** if those 1k were Web Search calls. Native function calling with the same 400-token payloads ≈ same **$2**, plus your infra. MCP loses when catalogs are **uncached and huge**, or when hosted MCP `tools/list` runs **every** new conversation without `mcp_list_tools` reuse.

### 6.6 Interview-ready invariants

1. Host ≠ client ≠ server; one client per server; the LLM never speaks JSON-RPC.  
2. `2026-07-28` is **stateless HTTP**: `_meta` + `Mcp-Method`/`Mcp-Name`; sessions are handles or Tasks.  
3. MRTR replaced bidirectional sampling/elicitation streams; `requestState` must be AEAD.  
4. Sampling/roots/logging/HTTP+SSE are **deprecated** (12-month floor), not gone today.  
5. MCP $ / 1k calls = **$0** protocol + **token** economics; Web Search is a **$10/1k** SKU. ⚠️ No MCP p99.  
6. Token **passthrough** is a spec violation; `aud` + RFC 8707 are Zero-Trust MCP.  
7. Tool text is an **instruction channel**; poisoning, shadowing, rug-pull, resource injection are production, not theory.  
8. A2A is the peer plane; MCP is the tool plane; gateways/registries are the enterprise control plane.  
9. Annotations, `cacheScope`, and first-party UIs that hide arguments will lie to humans.  
10. Multi-server **isolation of process** ≠ **isolation of prompt**.

---

## Sources

1. https://modelcontextprotocol.io/specification/2026-07-28  
2. https://modelcontextprotocol.io/docs/2026-07-28/getting-started/intro  
3. https://modelcontextprotocol.io/docs/2026-07-28/learn/architecture  
4. https://modelcontextprotocol.io/docs/2026-07-28/learn/client-concepts  
5. https://modelcontextprotocol.io/specification/2026-07-28/server/tools  
6. https://modelcontextprotocol.io/specification/2026-07-28/server/resources  
7. https://modelcontextprotocol.io/specification/2026-07-28/client/elicitation  
8. https://modelcontextprotocol.io/specification/2026-07-28/client/sampling  
9. https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/stdio  
10. https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http  
11. https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization  
12. https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/security-considerations  
13. https://modelcontextprotocol.io/specification/2026-07-28/basic/patterns/mrtr  
14. https://modelcontextprotocol.io/specification/2026-07-28/server/utilities/caching  
15. https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/security_best_practices  
16. https://modelcontextprotocol.io/docs/2026-07-28/develop/clients/client-best-practices  
17. https://modelcontextprotocol.io/extensions/auth/enterprise-managed-authorization  
18. https://modelcontextprotocol.io/extensions/auth/oauth-client-credentials  
19. https://modelcontextprotocol.io/extensions/tasks/overview  
20. https://modelcontextprotocol.io/registry/about  
21. https://blog.modelcontextprotocol.io/posts/2026-07-28/  
22. https://blog.modelcontextprotocol.io/posts/2025-09-08-mcp-registry-preview/  
23. https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/index.mdx  
24. https://www.jsonrpc.org/specification  
25. https://datatracker.ietf.org/doc/html/rfc8707  
26. https://datatracker.ietf.org/doc/html/rfc9728  
27. https://datatracker.ietf.org/doc/html/rfc9207  
28. https://datatracker.ietf.org/doc/html/draft-ietf-oauth-v2-1-13  
29. https://datatracker.ietf.org/doc/html/draft-ietf-oauth-client-id-metadata-document-00  
30. https://www.rfc-editor.org/rfc/rfc9068.html  
31. https://cursor.com/docs/mcp  
32. https://forum.cursor.com/t/oauth-redirect-uri-changed-from-cursor-to-http-localhost-for-streamable-http-mcp/165019  
33. https://claude.com/docs/connectors/overview  
34. https://claude.com/docs/connectors/building  
35. https://claude.com/docs/connectors/building/mcp  
36. https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp  
37. https://platform.claude.com/docs/en/agents-and-tools/mcp-connector  
38. https://code.claude.com/docs/en/mcp  
39. https://www.anthropic.com/pricing#api  
40. https://developers.openai.com/api/docs/guides/tools-connectors-mcp  
41. https://developers.openai.com/api/docs/guides/tools  
42. https://developers.openai.com/api/docs/pricing  
43. https://developers.openai.com/api/docs/models/gpt-5.6-sol  
44. https://developers.openai.com/cookbook/examples/mcp/mcp_tool_guide  
45. https://openai.com/api-fast-mode/  
46. https://code.visualstudio.com/docs/copilot/customization/mcp-servers  
47. https://a2a-protocol.org/latest/  
48. https://a2a-protocol.org/latest/topics/a2a-and-mcp/  
49. https://a2a-protocol.org/latest/specification/  
50. https://a2a-protocol.org/latest/announcing-1.0/  
51. https://blog.cloudflare.com/mcp-v2/  
52. https://github.com/microsoft/mcp-gateway  
53. https://aigateway.envoyproxy.io/release-notes/v1.0/  
54. https://github.com/MicrosoftDocs/azure-ai-docs/blob/main/articles/foundry/agents/concepts/tool-catalog.md  
55. https://github.com/modelcontextprotocol/registry  
56. https://registry.modelcontextprotocol.io  
57. https://invariantlabs.ai/blog/mcp-security-notification  
58. https://invariantlabs.ai/blog/mcp-github-vulnerability  
59. https://invariantlabs.ai/blog/introducing-mcp-scan  
60. https://arxiv.org/pdf/2601.07395  
61. https://github.com/advisories/GHSA-v8vw-gw5j-w7m6  
62. https://ts.sdk.modelcontextprotocol.io/v2/clients/oauth.html  
63. https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool  
64. https://developers.openai.com/api/docs/guides/tools-tool-search  
65. https://modelcontextprotocol.io/seps/1024-mcp-client-security-requirements-for-local-server-  
66. https://modelcontextprotocol.io/llms.txt  
67. https://csrc.nist.gov/pubs/sp/800/207/final  
