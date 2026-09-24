# Research: MCP & Integrations

**Date researched**: 2026-09-23
**Sources consulted**: 96

Vendor list prices, RPM/ITPM, and tokenizer IDs live in [`01-python-llm-foundations.md`](01-python-llm-foundations.md). Prefix-stability, cache breakpoints, and the **tool-schema tax** (Sonnet 5 hidden **354** / **474**; Cursor MCP **−46.9%**) live in [`02-context-engineering.md`](02-context-engineering.md). JSON Schema compilation, provider `strict` / `VALIDATED` subsets, Pydantic last-mile validation, semantic vs HTTP retry, `isError` vs JSON-RPC **−32602**, and MCP **page-1-only** client bugs live in [`03-tool-calling.md`](03-tool-calling.md). This file does **not** recopy those tables. It is the **protocol + integration plane**: JSON-RPC MCP (stdio / Streamable HTTP / legacy SSE), OAuth 2.1 + RFC 8707, SEP governance, OpenAPI→MCP gateways, webhooks vs MCP notifications, and Zero-Trust token rules.

Protocol authority is **`2026-07-28`** at [modelcontextprotocol.io](https://modelcontextprotocol.io/specification/2026-07-28). Prior dated revisions (`2024-11-05`, `2025-03-26`, `2025-06-18`, `2025-11-25`) remain in the wild; mix-version fleets are a production fact, not a lab edge. MCP itself publishes **no** p50/p95/p99 latency SLOs and **no** per-call SKU. ⚠️ Missing percentiles and unpublished “MCP surcharge” line items are marked, not invented.

Invariant: **the model never speaks MCP**. It emits a native `tool_use` / `function_call` / `functionCall`. The **host’s client** (or a hosted connector at Anthropic / OpenAI) translates to JSON-RPC `tools/call`. Cite **03** for the dispatcher contract; this module is what that dispatcher talks **to**.

---

## 1. System Topology & Mechanics

### 1.1 Three roles, one JSON-RPC hop

MCP is a **host / client / server** topology, not a two-node RPC ([architecture](https://modelcontextprotocol.io/specification/2026-07-28/architecture)):

| Role | What it is | Cardinality |
| --- | --- | --- |
| **Host** | The AI application the user sees (Cursor, Claude Desktop / claude.ai, VS Code Copilot, ChatGPT, a custom agent runtime). Owns UX, consent, tool-approval policy, multi-server orchestration, and the LLM conversation. | 1 per user session |
| **Client** | A protocol connector **inside** the host. One client ↔ one server. Instantiates a transport, carries `_meta`, maps `tools/list` into the model’s native tool schema. | N per host |
| **Server** | A process that exposes **tools** (model-invoked actions), **resources** (URI-addressed context), and **prompts** (templated workflows). Local (stdio) or remote (Streamable HTTP). | 1 per connection from a given client; remote servers multiplex many clients |

Hosted connectors invert the topology: Anthropic’s Messages API MCP connector and OpenAI’s Responses `type: "mcp"` make the **provider** the MCP client. Your app never opens a socket to the MCP server ([Anthropic MCP connector](https://platform.claude.com/docs/en/agents-and-tools/mcp-connector); [OpenAI MCP & connectors](https://developers.openai.com/api/docs/guides/tools-connectors-mcp)).

A2A is **not** this plane. Official A2A docs: MCP = agent→**tool/resource**; A2A = agent→**agent** (opaque peers, Agent Cards, task lifecycle). Complementary; do not flatten a multi-turn partner agent into `tools/call` ([A2A and MCP](https://a2a-protocol.org/latest/topics/a2a-and-mcp/); [A2A home](https://a2a-protocol.org/latest/)).

### 1.2 Two layers, two planes

Official docs split MCP into a **data layer** (JSON-RPC 2.0) and a **transport layer** (stdio or Streamable HTTP + HTTP auth) ([architecture](https://modelcontextprotocol.io/specification/2026-07-28/architecture); [JSON-RPC 2.0](https://www.jsonrpc.org/specification)). For enterprise design, remap those onto planes:

| Plane | MCP objects | Typical owners |
| --- | --- | --- |
| **Control** | `server/discover`; per-request `_meta` (`protocolVersion`, `clientInfo`, `clientCapabilities`); OAuth 2.1 / RFC 9728 discovery; `Mcp-Method` / `Mcp-Name` / `MCP-Protocol-Version` HTTP headers; gateway policy; `subscriptions/listen`; extension negotiation (`io.modelcontextprotocol/tasks`, EMA, MCP Apps) | Host, IdP, API gateway / MCP gateway |
| **Data** | `tools/call`, `resources/read`, tool `content` / `structuredContent`, resource blobs, elicitation payloads, Task poll/result | MCP server + upstream APIs |

JSON-RPC 2.0 is the message contract. Requests have `id` + `method` + `params`; notifications have no `id`. Protocol errors use JSON-RPC codes (`-32602` invalid params / unknown tool / resource not found; `-32603` internal). Tool **business** failures are **not** JSON-RPC errors: they are successful results with `isError: true` so the model can self-correct ([tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)). Host mapping of −32602 vs `isError` is **03** (SEP-2140, TS SDK v2, Claude Code page-1 bugs).

`2026-07-28` made the **control plane stateless**: the `initialize` / `initialized` handshake and `Mcp-Session-Id` are retired (SEP-2575, SEP-2567). Every request is self-describing. Optional `server/discover` is a cacheable capability dump, not a session open ([spec blog](https://blog.modelcontextprotocol.io/posts/2026-07-28/)). Application state that used to hide in the transport **must** become an **explicit handle** in tool arguments.

Header-based routing (SEP-2243): Streamable HTTP POSTs **MUST** carry `MCP-Protocol-Version`, `Mcp-Method`, and (for `tools/call`, `resources/read`, `prompts/get`) `Mcp-Name`. Gateways / WAFs can authorize **without parsing the JSON body** ([streamable-http](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http); [spec blog](https://blog.modelcontextprotocol.io/posts/2026-07-28/)).

### 1.3 Transports: stdio vs Streamable HTTP vs SSE

Three transports coexist in production hosts. Only two are specified as current; one is Deprecated.

**stdio** ([stdio spec](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/stdio)): host launches the server as a subprocess. Newline-delimited JSON-RPC on stdin/stdout; **MUST NOT** embed newlines in a message. stderr is logging only. No HTTP headers — `_meta` lives in the JSON body. Cancellation is `notifications/cancelled`. Shutdown: close stdin, wait, then SIGTERM→SIGKILL (POSIX) or `TerminateProcess` / Job Objects (Windows). Unexpected process death: client **SHOULD** restart; in-flight calls are lost; re-open `subscriptions/listen`. Probe with `server/discover` before falling back to legacy `initialize`. STDIO **SHOULD NOT** use the HTTP OAuth profile — credentials from the environment ([authorization](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization)).

**Streamable HTTP (`2025-03-26` through `2025-11-25`)** ([2025-11-25 transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)): one MCP endpoint (e.g. `https://example.com/mcp`) accepting **POST and GET**. Client `Accept: application/json, text/event-stream`. Server replies with JSON or an SSE stream. Optional `Mcp-Session-Id` (terminated with HTTP DELETE). Clients MAY GET the endpoint to open a standalone SSE stream for **server-initiated** JSON-RPC (elicitation, sampling, roots). Streams were resumable via `Last-Event-ID`. This is the shape OpenAI Responses still documents as “Streamable HTTP or HTTP/SSE” ([OpenAI MCP](https://developers.openai.com/api/docs/guides/tools-connectors-mcp)).

**Streamable HTTP (`2026-07-28`)** ([streamable-http](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http)): **POST-only**. GET / DELETE **SHOULD** return 405. Removed: protocol-level sessions, GET stream endpoint, `Last-Event-ID` resumability. Server-to-client RPC is **not** sent as independent JSON-RPC on the stream; it is embedded as `InputRequiredResult` (MRTR, SEP-2322). Long-lived change notifications use `subscriptions/listen` (SSE that stays open). Cancellation on HTTP = **close the SSE stream**; do not POST `notifications/cancelled`. Servers **SHOULD** send `X-Accel-Buffering: no` and SSE comment keep-alives (`:` lines) so nginx/CDNs do not buffer or idle-timeout the listen stream.

**Deprecated HTTP+SSE (`2024-11-05`)**: separate GET `/sse` plus a POST message endpoint; first SSE event is `endpoint`. Classified Deprecated under the feature lifecycle policy with a **12-month** minimum offramp (SEP-2596) ([spec blog](https://blog.modelcontextprotocol.io/posts/2026-07-28/); [changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog.md)). Cursor still documents `SSE` as a third transport ([Cursor MCP](https://cursor.com/docs/mcp)). Claude Code warns SSE is deprecated; the Platform MCP connector docs still present SSE and Streamable HTTP as peer options ([Claude Code #20307](https://github.com/anthropics/claude-code/issues/20307)).

**Origin / bind rules (HTTP):** validate `Origin` or 403 (DNS rebinding); local servers **SHOULD** bind `127.0.0.1` not `0.0.0.0` ([streamable-http](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http)). This is the spec-level control that CVE-2025-66414 showed SDKs were not enabling by default (§4.7, §5.2).

Back-compat probe (legacy HTTP+SSE): if modern POST fails with 4xx **and** the body is not a recognized modern JSON-RPC error, GET the URL expecting an `endpoint` event ([streamable-http](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http)). Clients supporting both 2025 sessions and 2026 stateless: probe `server/discover`; on `DiscoverResult` stay modern; on `UnsupportedProtocolVersionError` pick from `supported`; on timeout/other error **then** `initialize` — do not key fallback on one error code ([stdio](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/stdio)).

### 1.4 Capability negotiation (per request, not per session)

Every `2026-07-28` request **MUST** carry `_meta.io.modelcontextprotocol/protocolVersion`, and **SHOULD** carry `clientInfo` + `clientCapabilities` ([architecture](https://modelcontextprotocol.io/specification/2026-07-28/architecture)). Servers advertise `supportedVersions` and capabilities via `server/discover` (cacheable: `ttlMs`, `cacheScope`).

Client capabilities that matter in 2026:

- `elicitation.form` / `elicitation.url` — empty `elicitation: {}` ≡ form-only (compat).
- `sampling` / `sampling.tools` — **deprecated** as of `2026-07-28` but still on the 12-month clock; new work should call LLM APIs directly ([sampling](https://modelcontextprotocol.io/specification/2026-07-28/client/sampling)).
- `extensions`: `io.modelcontextprotocol/tasks`, `io.modelcontextprotocol/enterprise-managed-authorization`, MCP Apps.

Server capabilities: `tools.listChanged`, `resources.listChanged` / `resources.subscribe`, `prompts`, extensions. Tool lists **MUST NOT** vary as a side effect of other requests on a connection; they **MAY** vary by **authorization presented on that request** ([tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)).

### 1.5 Tools: list, call, schema, pagination, list_changed

Servers declaring `tools` **MUST** implement `tools/list` + `tools/call`. Lists are paginated, cacheable, and **SHOULD** be **deterministically ordered** to stabilize LLM prompt caches ([tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools); SEP-2549). Example in spec: `ttlMs: 300000` (5 min), `cacheScope: "public"`.

**Schema.** `inputSchema` **MUST** be a JSON Schema object; default dialect **JSON Schema 2020-12** if `$schema` is omitted (SEP-1613, SEP-2106). Optional `outputSchema` — if present, `structuredContent` **MUST** conform; clients **SHOULD** validate. Dual-write: structured results **SHOULD** also appear as serialized JSON in a `text` content block for older hosts. Parameterless tools: `{ "type": "object", "additionalProperties": false }` is the recommended empty object. Provider-subset compilation of that schema (OpenAI/Anthropic `strict`, Gemini OpenAPI-subset) is **03** — MCP-valid 2020-12 is **not** automatically `strict: true`-valid.

**Names.** 1–128 chars; `[A-Za-z0-9_.-]`; case-sensitive; unique **per server**. Aggregators **SHOULD** prefix with a **client-assigned** server id, not `serverInfo.name` (not globally unique) ([tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)).

**Call result types.** `resultType: "complete"` (normal / `isError`); `"input_required"` (MRTR); `"task"` if Tasks extension. Content types: `text`, `image`, `audio`, `resource_link`, embedded `resource`. Resource links from tools **need not** appear in `resources/list`.

**Pagination.** Opaque cursor. Request `tools/list` with optional `params.cursor`; continue using each response’s `nextCursor` **verbatim** until it is absent or null. Page size is **server-chosen**; clients **MUST NOT** assume a fixed size. Empty string is a **valid** cursor (not end-of-list). Invalid cursor → JSON-RPC **−32602**. Same model applies to `resources/list`, `resources/templates/list`, `prompts/list` ([pagination](https://modelcontextprotocol.io/specification/2026-07-28/server/utilities/pagination)). **03** documents the production trap: Claude Code historically fetched **page 1 only**; AgentCore gateways paginate at **30**/page → tools 31+ surface as `No such tool available` ([#39586](https://github.com/anthropics/claude-code/issues/39586); [#59538](https://github.com/anthropics/claude-code/issues/59538)).

**`notifications/tools/list_changed`.** If the server advertised `tools.listChanged`, it **SHOULD** emit this notification on the `subscriptions/listen` stream when the catalog changes (client opted in with `toolsListChanged: true`). Client then re-lists **all pages from cursor=null** ([tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools); [TS SDK notifications](https://github.com/modelcontextprotocol/typescript-sdk/blob/main/docs/servers/notifications.md)). On `2026-07-28`, change notifications **do not** ride a GET SSE; they ride the opt-in listen stream. `list_changed` **immediately** invalidates even inside `ttlMs` ([caching](https://modelcontextprotocol.io/specification/2026-07-28/server/utilities/caching)).

**`x-mcp-header`.** Primitive params (string/integer/boolean, not `number`) can be mirrored to `Mcp-Param-{name}` HTTP headers for WAF/LB routing. **MUST NOT** put secrets/PII there. Clients **MUST** drop tools whose `x-mcp-header` values violate RFC 9110 `tchar` rules ([tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)).

**Annotations.** Optional behavior hints. Spec: clients **MUST** treat annotations as **untrusted** unless the server is trusted. HITL: hosts **SHOULD** confirm invocations; tools are arbitrary code execution ([tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)).

### 1.6 Resources and prompts

**Resources** are URI-identified context (RFC 3986), not actions ([resources](https://modelcontextprotocol.io/specification/2026-07-28/server/resources)). Hosts choose UX: picker, search, auto-attach. Methods: `resources/list`, `resources/read`, `resources/templates/list`. Contents: `text` or base64 `blob`. `resources/read` **MAY** return multiple contents (directory). Missing resource: JSON-RPC `-32602` (not empty `contents[]`). `https://` URIs **SHOULD** be fetchable by the client directly; otherwise use `file://`, `git://`, or a custom scheme. Servers **MUST** sanitize `file://` paths (traversal). Templates use RFC 6570 URI templates. Annotations: `audience` (`user`|`assistant`), `priority` 0.0–1.0, `lastModified` ISO-8601 — **hints**, not authz. If `resources.subscribe`, client opens `subscriptions/listen` with resource URIs; server emits `notifications/resources/updated`. `resources/read` **MAY** return `InputRequiredResult`.

**Prompts** are user-controlled templates (`prompts/list`, `prompts/get`). Same pagination + `listChanged` + listen-stream pattern as tools. Hosts typically surface them as slash commands (VS Code `/.`) or picker items. Not currently exposed by Anthropic’s hosted MCP connector (tools only) ([MCP connector](https://platform.claude.com/docs/en/agents-and-tools/mcp-connector)).

### 1.7 Sampling, elicitation, MRTR

**MRTR (SEP-2322)** is the **only** legal way for a `2026-07-28` server to ask the client for elicitation, sampling, or roots — a breaking change from bidirectional SSE requests ([MRTR](https://modelcontextprotocol.io/specification/2026-07-28/basic/patterns/mrtr)). Flow: client `tools/call` (or `resources/read` / `prompts/get`) → server `resultType: "input_required"` with `inputRequests{}` + optional opaque `requestState` → client gathers input → **retry** the **same method** with a **new JSON-RPC id**, echoing `requestState` and attaching `inputResponses`. Servers **MUST** treat `requestState` as attacker-controlled: HMAC/AEAD, bind principal, TTL, bind originating method/args; single-use requires server-side enforcement. Servers **MUST NOT** send an `inputRequests` method the client did not declare.

**Elicitation** ([elicitation](https://modelcontextprotocol.io/specification/2026-07-28/client/elicitation); SEP-1036 URL mode):

- **form:** restricted flat JSON Schema (string/number/boolean/enum/multi-select). Data **is** visible to the client (and often the model). **MUST NOT** collect passwords, API keys, tokens, payment credentials.
- **url:** out-of-band navigation; client shows domain and gets consent; secrets **never** transit the MCP client. Use for OAuth-to-third-party and credential collection. URL **MUST NOT** contain PII or be pre-authenticated (impersonation if a malicious client opens it). Client **MUST NOT** prefetch; **MUST NOT** open without explicit consent. URL elicitation does **not** change the MCP client’s bearer token — it is server-to-third-party, not client-to-server auth.

Hosts **MUST** show **which server** is asking and allow decline/cancel.

**Sampling** ([sampling](https://modelcontextprotocol.io/specification/2026-07-28/client/sampling)): server asks the **client’s** LLM to complete via `sampling/createMessage` inside MRTR — no server-held API keys. Deprecated `2026-07-28` (SEP-2577); 12-month floor. `includeContext: "thisServer"|"allServers"` is separately deprecated. Security **SHOULD**s: human in the loop with deny; validate message content; rate-limit; handle sensitive data; iteration limits on nested `tools` / `toolChoice` if the client declared `sampling.tools`. New designs: server calls the model vendor directly, or the host uses programmatic / code-mode tool calling ([client best practices](https://modelcontextprotocol.io/docs/2026-07-28/develop/clients/client-best-practices)).

### 1.8 SEP process (how the protocol changes)

A **Specification Enhancement Proposal** is the design-doc path for substantial protocol, governance, or controversial change. Smaller diffs go as ordinary PRs. Standards Track SEPs that change observable protocol behavior need a conformance scenario + `sep-NNNN.yaml` traceability of MUST/SHOULD before `Final` ([SEP guidelines](https://mcp.mintlify.app/community/sep-guidelines)). SEPs cited in this file: SEP-1613/2106 (JSON Schema 2020-12), SEP-1036 (URL elicitation), SEP-1686/2663 (Tasks), SEP-2133 (extensions framework), SEP-2243 (`Mcp-Method`/`Mcp-Name`), SEP-2322 (MRTR), SEP-2352 (credential-issuer bind), SEP-2468 (RFC 9207 `iss`), SEP-2549 (cacheable lists), SEP-2567/2575 (stateless / no sessions), SEP-2577 (deprecate roots/sampling/logging), SEP-2596 (HTTP+SSE Deprecated), SEP-837 (`application_type` for localhost DCR), SEP-990 (EMA), SEP-1046 (client credentials), SEP-1024 (local-server client security), SEP-1865 (MCP Apps), SEP-414 (OTel `traceparent` in `_meta`).

### 1.9 Hosts and connectors in the market

| Host | Transport | What it actually is | Notes |
| --- | --- | --- | --- |
| **Cursor** | stdio, Streamable HTTP, legacy SSE | IDE host; one client per `mcp.json` entry | OAuth loopback `http://localhost:8787/callback` + cloud `https://www.cursor.com/agents/mcp/oauth/callback`; static `auth.CLIENT_ID`; enterprise allowlist + per-server network sandbox; one server crash does not take down others ([Cursor MCP](https://cursor.com/docs/mcp)) |
| **Claude.ai / Desktop / Cowork / Mobile** | Remote: Anthropic-brokered HTTP; Desktop also local stdio | Host; remote connectors egress from **Anthropic IPs**, not the laptop ([Help Center](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp)) | Directory + verification labels ([connectors overview](https://claude.com/docs/connectors/overview)) |
| **Claude API MCP connector** | Streamable HTTP or SSE; **tools only** | Anthropic is the MCP client (`mcp-client-2025-11-20` beta). No STDIO. Not on Bedrock/Vertex; Foundry only if hosted on Anthropic. ZDR: **not eligible** ([MCP connector](https://platform.claude.com/docs/en/agents-and-tools/mcp-connector)) | `mcp_servers[]` + `tools: [{type:"mcp_toolset"}]`; allow/deny via `default_config` / `configs`; up to **20** servers on managed agents |
| **OpenAI Responses / ChatGPT connectors** | Streamable HTTP or HTTP/SSE | OpenAI is the MCP client. `type: "mcp"` with `server_url` **or** `connector_id` **or** `tunnel_id` (Secure MCP Tunnel) ([OpenAI MCP](https://developers.openai.com/api/docs/guides/tools-connectors-mcp)) | `require_approval` default-on; `allowed_tools`; **no extra $ per MCP call**; MCP RPM **200 / 1000 / 2000** by tier |
| **VS Code Copilot** | stdio / `type: http` | Host; gallery + `.vscode/mcp.json`; macOS/Linux **stdio sandbox**; GitHub org policy ([VS Code MCP](https://code.visualstudio.com/docs/copilot/customization/mcp-servers); [sandbox](https://code.visualstudio.com/docs/agents/reference/mcp-configuration)) | Resources via Add Context; prompts as slash commands; MCP Apps inline; `sandboxEnabled` **not Windows** |

**Multi-server.** Hosts instantiate **independent** clients. Tool-name collisions are the host’s problem (prefix). Cross-server **prompt context is shared** — that is the shadowing attack surface (§5.3). Process isolation ≠ prompt isolation.

### 1.10 OpenAPI-to-MCP, gateway PEP, AgentCore, Cloudflare

**OpenAPI → MCP** is the production adapter for REST estates: one OpenAPI 3.x operation → one MCP tool; auth stays **outside** the model. Native LLM tools are JSON Schema (OpenAI/Anthropic) or OpenAPI-subset (Gemini) — cite **03** for the compiler. MCP `inputSchema` is 2020-12.

**Amazon Bedrock AgentCore Gateway** turns OpenAPI / Smithy into a managed MCP server. Dual auth: **inbound** (OAuth on the gateway) vs **outbound** (API key / OAuth client-credentials / IAM to the upstream). Target types include `open-api-schema`, `mcp-server`, Smithy, Lambda. Gateway URL shape: `https://<gateway-id>.gateway.bedrock-agentcore.<region>.amazonaws.com/mcp` ([AgentCore samples](https://github.com/awslabs/agentcore-samples/tree/main/06-workshops/02-AgentCore-gateway/02-transform-apis-into-mcp-tools); [CLI gateway.md](https://github.com/aws/agentcore-cli/blob/main/docs/gateway.md); [workshop](https://catalog.workshops.aws/strands/en-US/module-8-amazon-bedrock-agentcore/8-4-agentcore-gateway/8-4-2-openapi-to-mcp-tools)). AWS contributed the Tasks extension; AgentCore hosts the stateless core ([spec blog quote](https://blog.modelcontextprotocol.io/posts/2026-07-28/)).

**Cloudflare:** stateless MCP on Workers; `createMcpHandler` factory (SDK v2, one server instance **per request**); default route `/mcp`; Origin validation (403 on malformed/opaque/non-HTTP Origin); `allowedHostnames`; listen-stream `keepAliveMs` default **15,000**; `maxSubscriptions` default **1,024**. `openApiMcpServer()` exposes a large OpenAPI as two **code-mode** tools (`search` + `execute`); the spec stays **out** of the model context; host `request()` callback holds auth; generated code cannot `fetch()` directly ([Cloudflare MCP v2 blog](https://blog.cloudflare.com/mcp-v2/); [handler API](https://developers.cloudflare.com/agents/model-context-protocol/apis/handler-api/); [OpenAPI code-mode](https://developers.cloudflare.com/agents/model-context-protocol/guides/build-codemode-openapi-mcp-server/); [migrate SDK v2](https://developers.cloudflare.com/agents/model-context-protocol/guides/migrate-to-mcp-sdk-v2/)). Product MCP servers at `*.mcp.cloudflare.com/mcp` dual-speak 2026 and stateless 2025; `/sse` is a URL alias that returns **410 Gone** for legacy GET SSE ([cloudflare/mcp-server-cloudflare](https://github.com/Cloudflare/mcp-server-cloudflare)).

**Microsoft MCP Gateway:** K8s reverse proxy + tool registry + `POST /mcp` router; adapters at `/adapters/{name}/mcp`; session-affine routing for mixed fleets; control plane for deploy/update/delete ([mcp-gateway](https://github.com/microsoft/mcp-gateway); [docs](https://microsoft.github.io/mcp-gateway/)). Foundry catalogs MCP, OpenAPI, **and** A2A as distinct tool types, plus a **Toolbox** that fronts them as one MCP endpoint ([Foundry tool catalog](https://learn.microsoft.com/en-us/azure/ai-foundry/agents/concepts/tool-catalog)).

**Envoy AI Gateway `MCPRoute`:** multiplexes backends at `/mcp`; `toolSelector` include/exclude regex; prefixes aggregated names (e.g. `githubissue_read`); OAuth / API-key injection; authorization policies ([Envoy MCP](https://aigateway.envoyproxy.io/docs/capabilities/mcp/); [example YAML](https://github.com/envoyproxy/ai-gateway/blob/main/examples/mcp/mcp_example.yaml)).

**Registries.** Canonical metadata store: [registry.modelcontextprotocol.io](https://registry.modelcontextprotocol.io) ([about](https://modelcontextprotocol.io/registry/about); [GitHub](https://github.com/modelcontextprotocol/registry)). Reverse-DNS names bound by GitHub OAuth/OIDC, DNS, or HTTP proof. **Not** a malware scanner. Preview: breaking changes possible. Registry `$schema` drift has broken VS Code gallery validation (~7% of a 100-entry sample on stale schema URLs) ([registry #783](https://github.com/modelcontextprotocol/registry/issues/783)). Anthropic Connectors Directory and Cursor Marketplace are **aggregators**, not the protocol registry.

### 1.11 Webhooks vs MCP notifications (async jobs)

Three different push planes. Do not collapse them.

| Plane | Who initiates | Contract | Fit |
| --- | --- | --- | --- |
| **MCP request-scoped SSE** | Server, during one `tools/call` | `notifications/progress`, `notifications/message`; stream dies with the JSON-RPC result | Progress bars; not durable |
| **MCP `subscriptions/listen`** | Client opens; server pushes | `notifications/tools/list_changed`, `notifications/resources/updated`, optional `notifications/tasks` | Catalog / resource freshness; **not** Stripe events |
| **MCP Tasks** | Server returns `resultType: "task"` | Client polls `tasks/get` (`pollIntervalMs`); optional listen notifications; `tasks/update` for mid-flight elicitation; cooperative `tasks/cancel` ([Tasks](https://modelcontextprotocol.io/extensions/tasks/overview)) | CI, batch, human approval **inside** an agent turn |
| **SaaS webhook** (Stripe-style) | Third party POSTs your HTTPS endpoint | HMAC (`Stripe-Signature` `t=,v1=`), **2xx fast**, retry up to **3 days**, dedupe on `event.id` ([Stripe webhooks](https://docs.stripe.com/webhooks); [signatures](https://docs.stripe.com/webhooks/signatures)) | Payment confirmation minutes–days later; agent may be gone |

Webhook dual-path for long jobs: MCP `tools/call` creates the upstream job (returns `taskId` **or** a Stripe `PaymentIntent` id as a handle) **and** a public webhook receiver updates the same durable store. Agent polls Tasks / a `get_job` tool; it does **not** hold HTTP for the bank. Event gateways (Hookdeck-class) sit **in front of** the MCP server for verify/dedupe/queue — they are not an MCP primitive ([Hookdeck](https://hookdeck.com/blog/mcp-event-gateway)).

---

## 2. Token Economics & NFR Metrics

### 2.1 Protocol price: $0 / 1k MCP calls

MCP has **no** settlement layer. OpenAI: *“When you’re using the MCP tool, you only pay for tokens used when importing tool definitions or making tool calls. There are no additional fees involved per tool call.”* ([OpenAI MCP](https://developers.openai.com/api/docs/guides/tools-connectors-mcp)). Contrast a **metered hosted tool** on the same API: Web search is a **per-1k-call SKU** plus search-content tokens at model rates (quote the pricing page you bill against; see **01**). Anthropic’s hosted MCP connector has **no** published per-call surcharge either; you pay Messages tokens + whatever the remote server costs you.

So **$ per 1k tool calls, MCP vs native function calling:** if schemas and result sizes are equal, the **token bill is the same class**. Hosted MCP adds **provider-side** `tools/list` + remote RTT, still billed as tokens, not as a per-call SKU. Native client tools add **your** RTT instead of OpenAI/Anthropic’s. ⚠️ Neither vendor publishes a “MCP surcharge” line item.

OpenAI **MCP-specific RPM** (Responses MCP tool): Tier 1 **200 RPM**; Tiers 2–3 **1000 RPM**; Tiers 4–5 **2000 RPM** ([OpenAI MCP usage notes](https://developers.openai.com/api/docs/guides/tools-connectors-mcp)). ⚠️ No matching Anthropic MCP-connector RPM table was found in the connector doc.

### 2.2 Schema tax of MCP catalogs (cite 02 / 03)

MCP hosts that dump every `tools/list` into the model **pay descriptor tokens on every turn** until prompt cache hits. This is the same tax **02** measures for inlined tools (Sonnet 5 hidden **354** / **474**) and **03** measures for MCP vs deferred discovery.

**Anthropic’s published five-server example** (tool search launch): GitHub 35 tools ~**26k**; Slack 11 ~**21k**; Sentry 5 ~**3k**; Grafana 5 ~**3k**; Splunk 2 ~**2k** → **58 tools ≈ 55k tokens** before the user types. Jira alone ~**17k**. Internal Anthropic peak cited: **134k** definition tokens before optimization. Tool search: search tool ~**500** + 3–5 hot tools ~**3k** → ~**8.7k**; claimed **>85%** cut and Opus 4 MCP-eval **49% → 74%** ([advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use); [tool search](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool)). You still **send every definition** every request; `defer_loading: true` controls what enters the **system-prompt prefix**. **Never** set `cache_control` on a deferred tool → 400. Details and the 3–5 hot-tool rule: **03**.

**Cursor A/B (runs that called an MCP tool):** sync descriptions to a folder; static prompt holds **names**; agent reads schema files on demand → **−46.9%** total agent tokens (statistically significant; high variance with MCP count) ([Cursor dynamic context discovery](https://cursor.com/blog/dynamic-context-discovery); [forum](https://forum.cursor.com/t/what-do-your-attached-mcp-servers-actually-cost-you-in-tokens-per-request-i-measured-it/166405)). This is a **schema-tax** reduction, not a model-price change (**02**).

**GitHub official MCP — do not quote “55k / 93 tools” as current without a pin.** That community count (Piotr Hajdas / Ken Imoto, 2026) described a full-surface snapshot ([Unblocked](https://getunblocked.com/blog/github-mcp-token-cost/)). GitHub maintainers then shipped **default toolsets**: **101 tools / 64.6k → 52 / 30.3k** (−49% tools, −53% tokens) ([discussion #1182](https://github.com/github/github-mcp-server/discussions/1182)). ContextTax (Anthropic `count_tokens`, pinned image): default toolset **43 tools / 10,928** Claude tokens; all toolsets **82 / 20,404** ([ContextTax](https://github.com/PavelTkachenk0/ContextTax)). Remote URLs: `https://api.githubcopilot.com/mcp/` (default) vs `/mcp/x/all` ([remote-server.md](https://github.com/github/github-mcp-server/blob/main/docs/remote-server.md)). OpenAI’s own docs point ChatGPT connectors at `https://api.githubcopilot.com/mcp/` rather than a third-party “GitHub MCP” proxy ([OpenAI MCP](https://developers.openai.com/api/docs/guides/tools-connectors-mcp)).

Official client guidance: if tool definitions exceed **~1–5% of the context window**, switch to progressive discovery (`search_tools` → `get_tool_details` → execute) or vendor tool-search ([client best practices](https://modelcontextprotocol.io/docs/2026-07-28/develop/clients/client-best-practices); [OpenAI tool search](https://developers.openai.com/api/docs/guides/tools-tool-search)). OpenAI: servers with **dozens** of tools cause “high cost and latency”; use `allowed_tools`.

### 2.3 Worked `$ / 1k` MCP-using turns **[inferred]**

List prices from **01** (2026-09-23): Claude Sonnet 5 **$2 / $0.20 cached / $10 out** per MTok; Claude Opus 5 **$5 / $0.50 / $25**; GPT-5.4 **$2.50 / $0.25 / $15**. Apply them to MCP catalogs; do not treat as a vendor MCP SKU.

| Path | Uncached input $ (Sonnet 5 / Opus 5) | Cache-read $ | Notes |
| --- | --- | --- | --- |
| Anthropic 55k five-server catalog / turn | 55k/1e6 × $2 = **$0.110** / × $5 = **$0.275** | × $0.20 = **$0.011** / × $0.50 = **$0.028** | Paid **every turn** if the `tools` array is in the cached prefix and **stable** |
| Same after tool search (~8.7k live) | **$0.017** / **$0.044** | **$0.0017** / **$0.0044** | Still send full defs on the wire; prefix is small |
| Cursor −46.9% on an MCP-calling run | apply to **agent tokens on those traces**, not to list price | n/a | High variance with server count |
| 1k `tools/call` results @ 800 tokens each through the model | 800k × $2/1e6 = **$1.60** input (Sonnet) + output | n/a | Code-mode/sandbox filters this **out** of the LLM |
| Same 1k calls, MCP protocol fee | **$0** | — | OpenAI statement above |

**[inferred] 1k-call economics (Sonnet 5, 400-token args+result average, schemas cached at 0.1×):** ~0.4 MTok in ≈ **$0.08 uncached-equivalent if cached** + output vs **$0.80** if the 55k catalog is **uncached every turn** on top of the calls. MCP loses when catalogs are **uncached and huge**, or when hosted MCP `tools/list` runs **every** new conversation without list reuse.

**Prompt-cache interaction:** adding/removing tools mid-conversation **invalidates** the prefix cache; a miss can cost more than the tools you dropped. Mitigations: deterministic `tools/list` order; append new defs after the cache breakpoint; or a single stable `call_tool({name,args})` meta-tool; disconnect servers at **conversation boundaries**, not per turn ([tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools); [client best practices](https://modelcontextprotocol.io/docs/2026-07-28/develop/clients/client-best-practices); **02**). `ttlMs` on list results cuts **MCP** refetch (cheap vs LLM tokens) but still matters for hosted-MCP first-byte latency.

**Programmatic / code-mode:** host compiles MCP schemas to sandbox functions; only `console.log` / summary returns to the model. Cloudflare `openApiMcpServer` is this pattern for OpenAPI. Security: sandbox has **no** network; host brokers `tools/call` and retains credentials.

### 2.4 Latency NFRs — published vs ⚠️

| Metric | Published? | Value / note |
| --- | --- | --- |
| MCP `tools/call` p50/p95/p99 | ⚠️ **No** | Dominated by upstream API + model round-trip, not JSON-RPC framing |
| Extra MCP RTT (hosted) | Qualitative | OpenAI cookbook: runtime does `tools/list` then model; `allowed_tools` reduces that |
| Approval vs no-approval | Qualitative | OpenAI: skip approvals (`require_approval: "never"`) for “reduced latency” after trust |
| SSE buffering | Spec MUST/SHOULD | Missing `X-Accel-Buffering: no` → proxy holds events; looks like multi-second p99 |
| Stream resume | Spec `2026-07-28` | **Not supported** (`Last-Event-ID` removed) — reconnect = replay from app handles / Tasks |
| Cloudflare listen keep-alive | Product default | `keepAliveMs` **15,000** |
| Honeycomb anecdote | Qualitative | ~**20%** of monthly interactive queries via MCP after spec change ([spec blog](https://blog.modelcontextprotocol.io/posts/2026-07-28/)) — not a latency SLO |
| SDK scale (not latency) | Spec blog | ~**0.5B** Tier-1 SDK downloads / month; TS and Python **>1B** lifetime |

**[inferred] latency budget** for a remote `tools/call` (order-of-magnitude, not SLO): TLS+auth 20–80 ms; JSON-RPC 1–5 ms; extra hosted `tools/list` one RTT on cold conversation; upstream API 50–2000 ms; model think+decode 500 ms–tens of s. Optimize the **upstream**, **catalog size**, and **approval UI**, not the RPC codec. Stateless `2026-07-28` removes sticky-session p99 spikes from session-store failover (GitHub MCP dropped Redis after upgrade — spec blog).

**Availability:** MCP spec defines error mapping, not 99.9%. Cursor: failed MCP call is isolated; other servers continue ([Cursor](https://cursor.com/docs/mcp)).

---

## 3. Distributed Resilience & State

### 3.1 Stateless core + explicit handles

Any `2026-07-28` request can land on any replica behind round-robin; **no** shared session store required ([spec blog](https://blog.modelcontextprotocol.io/posts/2026-07-28/); [Cloudflare MCP v2](https://blog.cloudflare.com/mcp-v2/)). Cross-call state = **handle in arguments** (cart id, browser context id, Stripe `pi_…`). Handle rules ([tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)):

- Authenticated: handle is a **name**, not a capability — re-check authz every call.
- Unauthenticated: handle **is** a bearer token → UUIDv4-class entropy + TTL.
- Opaque; document lifetime in the **create** tool description (model-visible).
- Unknown/expired → `isError: true` with a recoverable message (create a new handle).

Legacy `Mcp-Session-Id` still appears in mixed fleets (2025 Streamable HTTP, some TS SDK `terminateSession` helpers). Sticky sessions against a stateless farm → 4xx / lost elicitation. Dual-speak `/mcp` (Cloudflare, AgentCore) is the migration pattern.

### 3.2 Reconnect, cancellation, subscriptions

| Event | stdio | Streamable HTTP 2025-11-25 | Streamable HTTP 2026-07-28 |
| --- | --- | --- | --- |
| Cancel in-flight | `notifications/cancelled` | Close SSE **or** cancel notification | Close SSE; no cancel notification |
| Process/replica death | Restart subprocess; retry calls; re-`listen` | Session lost unless shared store | Retry POST on any instance; in-flight SSE lost |
| Catalog/resource change | `subscriptions/listen` (2026) / connection notify (older) | GET SSE + sessions | `subscriptions/listen`; keep-alives required |
| SSE disconnect | n/a | `Last-Event-ID` resume | Re-`listen`; **no** replay |
| Session DELETE | n/a | `Mcp-Session-Id` terminate | Header **gone** |

### 3.3 At-least-once tools

JSON-RPC retries after transport failure are **at-least-once**. The protocol has no idempotency key. Production rule (cite **03** for Stripe `Idempotency-Key` and semantic vs HTTP retry):

- **Safe to retry:** GET-equivalent tools, `tools/list`, `tasks/get`, `resources/read` of immutable URIs.
- **Not safe:** `create_charge`, `send_email`, `drop_table` — mint a client idempotency key **as a required tool argument** and persist it server-side; map duplicates to the original result (`isError: false` with the first receipt), not a second side effect.
- Unknown-tool **−32602** after a `list_changed` is a **catalog** problem, not a retry-storm: re-list all pages once, then fail to the model with the allowlist (**03**).
- Do not retry `isError: true` business failures with HTTP backoff; that is the model’s semantic retry.

**[inferred] client circuit:** after N consecutive transport failures, trip the server for T seconds, keep others; do not poison the model with a 50-retry loop. Cursor already isolates crash/timeout per server.

### 3.4 Tasks extension (durable work)

`io.modelcontextprotocol/tasks` (SEP-1686 / SEP-2663; contributed with AWS): server returns `resultType: "task"` + `taskId`, `ttlMs`, `pollIntervalMs`. Client `tasks/get` until `completed|failed|cancelled`; `input_required` + `tasks/update` for mid-flight elicitation; optional `notifications/tasks` on listen. **Crash resilience:** persist `taskId`; poll after reconnect. Cancellation is **cooperative**. Do not hold HTTP for CI/batch ([Tasks](https://modelcontextprotocol.io/extensions/tasks/overview)). Server **MUST NOT** return a task to a client that did not declare the extension. Poll storms: honor `pollIntervalMs`; prefer notifications when advertised.

### 3.5 Webhook dual-path (SaaS long jobs)

Pattern for Stripe-class work:

1. Agent `tools/call` `create_checkout` → server creates Stripe Session, writes `(taskId, session_id, tenant)` to a store, returns `resultType: "task"` (or a pollable handle if the client lacks Tasks).
2. Stripe POSTs `checkout.session.completed` to an event gateway / your webhook. Verify `Stripe-Signature` on the **raw** body; insert `event.id` UNIQUE; return **2xx** before accounting work; Stripe retries non-2xx up to **three days** and **re-signs** each attempt ([Stripe webhooks](https://docs.stripe.com/webhooks)).
3. Webhook handler updates the same task row → `completed`. Agent’s next `tasks/get` (or `notifications/tasks`) sees the result.
4. If the webhook is lost, a reconciler `retrieve`s the Stripe object by stored id (source of truth is the API, not the payload).

MCP listen streams **cannot** replace this: they die with the client process; Stripe events arrive when no agent is connected.

### 3.6 Gateway resilience and caching

**Not in the MCP spec.** Hosts/gateways supply it. Observed patterns: Cursor isolation; Envoy 1.0 steers around rate-limited MCP backends; Microsoft gateway runs multiple router instances (session affinity — a **2025-era** assumption that becomes optional on 2026-07-28). Progressive discovery connects Salesforce only when `enable_server` fires; disconnect at task end ([client best practices](https://modelcontextprotocol.io/docs/2026-07-28/develop/clients/client-best-practices)).

**Caching as resilience:** `ttlMs` + `cacheScope`. Serve **stale** on refetch error (spec allows). `list_changed` **immediately** invalidates even inside TTL. `public` lists may be shared across tokens — never mark per-user catalogs `public`. Paginated lists: per-page TTL, no snapshot guarantee; invalid cursor → drop all pages ([caching](https://modelcontextprotocol.io/specification/2026-07-28/server/utilities/caching)).

MRTR `requestState` is how elicitation survives load-balanced retries. Integrity-protect it or an attacker forges “already approved.” One-time redemptions need a **server** nonce store — the only required shared state for that pattern ([MRTR](https://modelcontextprotocol.io/specification/2026-07-28/basic/patterns/mrtr)).

---

## 4. Enterprise Security & Governance

**This is the home topic.** MCP’s own spec says the protocol **cannot** enforce consent; implementors **SHOULD** ([spec](https://modelcontextprotocol.io/specification/2026-07-28)). Zero-Trust for MCP means: **never trust the tool catalog, the resource body, the annotation, the token audience, or the peer’s `cacheScope`.** Map to NIST SP 800-207: authenticate every request, authorize per-action, assume breach, log everything ([NIST SP 800-207](https://csrc.nist.gov/pubs/sp/800/207/final)). **[inferred]** MCP is a new PEP/PDP pair in front of existing APIs. Gateways (Envoy `MCPRoute`, Microsoft MCP Gateway, AgentCore, Cloudflare Worker) are the practical PEP; `Mcp-Name` is the action name.

### 4.1 Trust principles in the spec

1. **User consent and control** — explicit, revocable, UI-visible.
2. **Data privacy** — hosts must not ship user data to servers or onward without consent.
3. **Tool safety** — tools = arbitrary execution; **descriptions/annotations untrusted** unless the server is trusted; confirm before invoke ([security best practices](https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/security_best_practices)).

Claude Code: servers that fetch external content expose **prompt-injection** risk ([Claude Code MCP](https://code.claude.com/docs/en/mcp)). OpenAI: malicious remote MCP can **exfiltrate anything in model context**; defaults to per-call approval; report `security@openai.com` ([OpenAI MCP risks](https://developers.openai.com/api/docs/guides/tools-connectors-mcp)).

### 4.2 OAuth 2.1 profile (HTTP only)

STDIO **SHOULD NOT** use this profile ([authorization](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization)). HTTP **SHOULD**.

**Standards stack:** OAuth 2.1 [draft-13](https://datatracker.ietf.org/doc/html/draft-ietf-oauth-v2-1-13), RFC 6750 Bearer, RFC 8414 AS metadata **or** OIDC Discovery, RFC 9728 Protected Resource Metadata (**MUST** on MCP servers), RFC 8707 `resource` parameter (**MUST** on clients), RFC 9207 `iss` on the auth response (SEP-2468), CIMD ([draft-ietf-oauth-client-id-metadata-document](https://datatracker.ietf.org/doc/html/draft-ietf-oauth-client-id-metadata-document-00)) **SHOULD**; RFC 7591 DCR **deprecated**, retained for compat ([authorization](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization); [authz security](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/security-considerations); [WorkOS](https://workos.com/blog/what-is-mcp-authorization)).

**Flow (compressed):** unauth MCP request → `401 WWW-Authenticate: Bearer resource_metadata=…, scope=…` → fetch PRM → AS metadata → CIMD (HTTPS `client_id` URL) or static/DCR → PKCE **S256** (refuse if `code_challenge_methods_supported` absent — including on OIDC metadata) → authorize with `resource` = MCP server canonical URI → validate `iss` → token with `resource` again → Bearer to MCP.

**Scope strategy:** challenge `scope` is authoritative for **this** operation; step-up rather than asking `scopes_supported` maximally. Least privilege is a spec SHOULD.

**Hardening `2026-07-28`:** bind client credentials to the issuing AS (SEP-2352); `application_type` on DCR so localhost redirects work for CLI (SEP-837); CIMD SSRF on the AS when fetching client metadata.

Cursor OAuth: RFC 8252 loopback; register **both** redirect URIs; `mcp.json` `auth` only `CLIENT_ID` / `CLIENT_SECRET` / `scopes` — no `redirect_uri` field ([Cursor MCP](https://cursor.com/docs/mcp)).

### 4.3 RFC 8707 resource indicators (audience)

MCP clients **MUST** send `resource` on **both** authorization and token requests, identifying the MCP server’s canonical URI, **whether or not the AS supports it** ([authorization](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization); [RFC 8707](https://www.rfc-editor.org/rfc/rfc8707.html)). MCP servers **MUST** validate that access tokens were issued specifically for them (RFC 8707 §2 / RFC 9068 `aud`). This is what stops a token minted for Server A from being spent at Server B ([WorkOS](https://workos.com/blog/what-is-mcp-authorization); [PolicyLayer](https://policylayer.com/attacks/token-mis-redemption)). Mix-up (evil AS harvests a code for an honest AS) is **not** solved by PKCE or resource indicators alone — RFC 9207 `iss` is the control ([authz security](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/security-considerations)).

### 4.4 Token passthrough is forbidden

MCP servers **MUST** accept only tokens **audienced to themselves** and **MUST NOT** forward the inbound access token to upstream APIs. Upstream = a **new** token from the upstream AS (on-behalf-of / client-credentials / workload identity / AgentCore outbound OAuth) ([security best practices](https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/security_best_practices)). Spec quote of the anti-pattern: accepting tokens “without validating that the tokens were properly issued to the MCP server and pass[ing] them through to the downstream API.”

Why ZT cares:

- Passthrough **bypasses** MCP-layer rate limits, schema validation, and audit (downstream logs show the wrong actor).
- A token stolen for Service A becomes a confused-deputy key for Service B if B doesn’t check `aud`.
- Future controls (step-up, tool-level RBAC) are unimplementable if the server is a dumb pipe.
- URL elicitation exists **because** having the MCP client obtain third-party tokens and hand them to the server **is** passthrough (SEP-1036 rationale).

**Short-lived access tokens; rotate refresh tokens for public clients** (OAuth 2.1). Store tokens in OS keychain / confidential store, not `mcp.json` plaintext. Cursor interpolation: `${env:NAME}` ([Cursor](https://cursor.com/docs/mcp)).

### 4.5 PAT, API keys, and when OAuth is the wrong tool

| Credential | Where it is legal | Failure if misused |
| --- | --- | --- |
| **OAuth 2.1 + PKCE + RFC 8707** | Remote Streamable HTTP; user-delegated SaaS | Mix-up / deputy if `iss`/`aud` skipped |
| **Static Bearer / PAT** | stdio env; OpenAI `authorization` on `type:"mcp"`; Anthropic `authorization_token`; Envoy `securityPolicy.apiKey`; Cursor `headers` | Long-lived; no audience; git leakage; cannot step-up |
| **API key to upstream** | Gateway **outbound** (AgentCore `api-key`, Cloudflare host `request()`) — **never** the inbound MCP token | Putting the upstream key in the model’s tool args |
| **GitHub PAT in stdio env** | Local IDE, least-privilege scopes, one repo | Invariant GitHub-issue flow still applies if the **model** sees public issues (§5.1) |

OpenAI connectors take an OAuth access token **your app** already has (`authorization` parameter) — that is **not** the MCP server performing OAuth; it is the host injecting a Bearer. Treat it as a PAT from the MCP server’s point of view: still audience-check if the MCP server is yours; still no passthrough to a **second** API.

### 4.6 Confused deputy (OAuth-proxy variant) — spec-normative

When an MCP **proxy** uses a **static** third-party `client_id`, allows **DCR** of MCP clients, and the third-party AS sets a **consent cookie**, an attacker registers `redirect_uri=attacker.com`, sends a link, and the cookie skips consent → attacker receives the MCP auth code ([security best practices](https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/security_best_practices)).

**MUST:** per-`client_id` consent **before** redirecting to the third party; exact `redirect_uri` match; CSRF/`state` issued **after** MCP consent; `__Host-` cookies; `frame-ancestors` / `X-Frame-Options: DENY`. Distinct from the **tool-confused-deputy** (server holds a powerful token and the **model** is injected) — both are “deputy” problems; mitigations differ (OAuth consent vs tool-flow policy). See §5.1.

### 4.7 DNS rebinding and CVE-2025-66414

Spec control: `Origin` 403 + bind loopback ([streamable-http](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http)).

**CVE-2025-66414 / GHSA-w48q-cv73-mx4w** (published 2025-12-02, CWE-350 / CWE-1188, High): `@modelcontextprotocol/sdk` **< 1.24.0** did **not** enable DNS rebinding protection by default for HTTP servers. Unauthenticated `StreamableHTTPServerTransport` or `SSEServerTransport` on localhost without `enableDnsRebindingProtection` → a malicious website rebinds DNS to `127.0.0.1` and invokes local tools. **stdio is unaffected.** Fix: **≥ 1.24.0**; `createMcpExpressApp()` enables host validation when binding localhost; custom Express must apply `hostHeaderValidation(['localhost','127.0.0.1','[::1]'])`. Binding `0.0.0.0` does **not** auto-enable protection (console warning) ([GHSA](https://github.com/advisories/GHSA-w48q-cv73-mx4w); [advisory](https://github.com/modelcontextprotocol/typescript-sdk/security/advisories/GHSA-w48q-cv73-mx4w); [OSV](https://osv.dev/vulnerability/GHSA-w48q-cv73-mx4w)). Cloudflare Workers wrappers validate every present Origin and 403 malformed/opaque/non-HTTP Origins ([migrate SDK v2](https://developers.cloudflare.com/agents/model-context-protocol/guides/migrate-to-mcp-sdk-v2/)).

### 4.8 Sampling security (deprecated but live)

Sampling lets a **server** steer the **client’s** model. Threats: prompt injection in `messages` / `systemPrompt`; nested tool loops as a token-drain / exfil gadget; `includeContext: "allServers"` leaking other servers’ data into the sampled prompt (deprecated for a reason). Spec **SHOULD**s: HITL deny; validate content; rate-limit; iteration limits on both sides; treat sampling payloads as untrusted ([sampling](https://modelcontextprotocol.io/specification/2026-07-28/client/sampling)). New work: do not declare `sampling`; call the vendor API from the server with **server-held** keys and a separate audit trail — or keep generation in the host.

### 4.9 EMA, RBAC, PII, sandbox, audit

**EMA** (`io.modelcontextprotocol/enterprise-managed-authorization`, SEP-990): employee SSO to the **host**; IdP issues **ID-JAG**; MCP AS exchanges ID-JAG for an MCP access token. Policy lives in Okta/Entra. Revoke at the IdP once ([EMA](https://modelcontextprotocol.io/extensions/auth/enterprise-managed-authorization)). Machine-to-machine: OAuth client-credentials extension (SEP-1046).

**RBAC.** Protocol primitive = OAuth **scopes** + per-request filtered `tools/list` / `resources/list`. Hosts add a second gate: Cursor **tool allowlists** inside an MCP allowlist (empty = all tools on that server); Claude API `mcp_toolset` allow/deny; OpenAI `allowed_tools` + `require_approval`; Foundry Toolbox: Entra + Azure Policy. **[inferred]** Fine-grained “this agent may `issues.write` on repo X only” is **not** in MCP — encode it in the server’s token exchange / ABAC, or a gateway `Mcp-Name` policy.

**PII.** Treat `resources/read`, tool args, and form elicitation as PII pipes. Ban secrets from form mode and from `x-mcp-header`. OpenAI: URLs/images from tool output are SSRF/exfil vectors; ZDR/data-residency **stops at the MCP hop** — the third party has its own retention. Anthropic remote connectors: data leaves the enterprise network to Anthropic **then** to the server — private-VPC MCP **will not** work for claude.ai; use Desktop stdio or OpenAI Secure MCP Tunnel. Anthropic connector: **ZDR not eligible** ([MCP connector](https://platform.claude.com/docs/en/agents-and-tools/mcp-connector)).

**Sandbox.** Spec: stdio is full local code exec. VS Code: `sandboxEnabled` + filesystem/network allowlists (macOS/Linux; **not Windows**); sandboxed tool calls auto-approve ([VS Code sandbox](https://code.visualstudio.com/docs/agents/reference/mcp-configuration)). Cursor enterprise: per-server network Allow all / Allowlist / Deny all / No sandbox; User MCP denylist ([Cursor](https://cursor.com/docs/mcp)). Code-mode: deny-all net; broker only. SEP-1024: client security requirements for **local** server install.

**SSRF (OAuth discovery).** Malicious `resource_metadata` → `http://169.254.169.254/`. Clients **SHOULD** HTTPS-only (loopback exception), block RFC 1918 / link-local / ULA, not follow redirects to internals. Do **not** hand-roll IP parsers. CIMD makes the **AS** an SSRF client too (RFC 9728 §7.7).

**Audit.** Spec: clients **SHOULD** log tool usage. OpenAI `store=true` retains 30 days unless ZDR. Gateways are the practical place for **immutable** tool-call logs with `Mcp-Method`/`Mcp-Name`. Logging utility on MCP itself is **deprecated** — use stderr (stdio) or OpenTelemetry (SEP-414).

### 4.10 Zero-Trust MCP control-plane checklist

| Control | Where |
| --- | --- |
| Strong identity (workforce SSO / workload identity) | EMA or CIMD+PKCE; no long-lived static Bearer in git |
| Per-request authz (scope + tool name + resource URI) | Gateway on `Mcp-Name` + server-side check; never trust annotations |
| Audience-bound tokens | RFC 8707 `resource`; reject wrong `aud` |
| No token passthrough | New upstream credential every hop |
| Least-privilege catalogs | Filtered `tools/list`; `allowed_tools`; progressive discovery |
| Network egress policy | Cursor/VS Code sandbox; Origin 403; CVE-2025-66414 patched SDK |
| Supply-chain pin | Hash tool descriptors; registry namespace proof; prefer first-party hosts (`mcp.stripe.com`, `api.githubcopilot.com`) |
| Assume poisoned catalog | Show full descriptions in HITL; pin versions; `list_changed` = re-review |
| Telemetry | OTel `traceparent` in `_meta` (SEP-414); gateway access logs |
| Revocation | IdP session kill (EMA) or refresh rotation; handle TTL |

---

## 5. Production Failure Modes

### 5.1 Confused deputy (two species)

**A. OAuth proxy deputy** — §4.6. Symptom: attacker holds an MCP token the user never granted to *that* client. Fix: per-client consent, exact redirect, no cookie-skip.

**B. Tool-authority deputy** — MCP server holds GitHub/Slack/DB credentials; the **model** is induced (via issue text, email, or another tool result) to use them. Invariant: official GitHub MCP + public issue → agent dumps private-repo PII into a public PR. **Not a bug in GitHub’s MCP code**; any client with that server is exposed. Mitigation: **one repo per session**, least-privilege PATs, runtime dataflow policy — GitHub cannot patch this alone ([GitHub MCP exploited](https://invariantlabs.ai/blog/mcp-github-vulnerability)).

Token passthrough **is** a deputy amplifier: downstream trusts a token the MCP server never validated.

### 5.2 DNS rebinding (browser → localhost MCP)

Mechanism: attacker page at `evil.example` rebinds the name to `127.0.0.1` after the browser has treated it as a remote origin, then POSTs JSON-RPC to the local Streamable HTTP server. Spec Origin check should 403; CVE-2025-66414 showed the **default SDK off**. Bind `0.0.0.0` + no auth + HTTP = the full blast radius (invoke any local tool, read any local resource). stdio is out of scope because there is no HTTP server. Detection: Host/Origin 403 spikes; unexpected `tools/call` from browser UAs. Fix: patch SDK, `hostHeaderValidation`, never expose unauthenticated localhost HTTP, prefer stdio for local secrets.

### 5.3 Tool name collision and shadowing

Uniqueness is **per server**. Two `search` tools at a host become one prompt namespace unless the host prefixes. Invariant **tool shadowing**: a malicious server’s `description` instructs the model to BCC an attacker on the **trusted** server’s `send_email` — the malicious tool need not even be called ([TPA](https://invariantlabs.ai/blog/mcp-security-notification); [mcp-injection-experiments](https://github.com/invariantlabs-ai/mcp-injection-experiments)). Envoy-style prefixing (`githubissue_read`) reduces **accidental** collision; it does **not** isolate **prompt** context. Mitigation: prefix with a **client-assigned** id (not `serverInfo.name`); isolate high-privilege servers into **separate hosts/conversations**; never mix an unvetted marketplace server with a secrets-bearing server.

### 5.4 Stale `tools/list` and pagination dropout

| Failure | Mechanism | Detection |
| --- | --- | --- |
| Page-1-only client | Ignores `nextCursor` | Tools 31+ “unknown”; AgentCore 30/page (**03**, Claude Code #39586) |
| Stale cache inside `ttlMs` | No `subscriptions/listen` / ignore `list_changed` | `tools/call` −32602 for a tool that exists now, or a **rug-pulled** tool that should be gone |
| OpenAI `mcp_list_tools` in context | List not refetched while the item is in context | Miss a rug-pull **or** stick to a stale good list |
| Cursor deferred files vs live list | Folder snapshot vs server | Auth-expired tools “disappear” from awareness unless file metadata carries status ([Cursor blog](https://cursor.com/blog/dynamic-context-discovery)) |
| Invalid cursor after catalog rewrite | Opaque cursor bound to an old snapshot | −32602; drop all pages and restart |

**Rug-pull:** benign `tools/list` at install-time approval, then `list_changed` (or silent mutation if the client never re-shows UI) injects poisoning. `ttlMs` + listen notifications make **detection** easier if the host **hashes** the list and **re-prompts** on diff. MCP-Scan “tool pinning” is this hash ([MCP-Scan](https://invariantlabs.ai/blog/introducing-mcp-scan)). Registry namespace auth stops **name squatting**, not **post-publish behavior**.

### 5.5 Oversized resources and schema blow-ups

`resources/read` of a directory, a blob, or a “helpful” log file can dump **megabytes** into the next model turn. Spec allows multiple contents per read. There is **no** protocol max payload. **[inferred]** host caps: byte-size + token-count before inject; offload to a file (Cursor’s long-tool-output pattern, **02**) and pass a URI. `audience: ["assistant"]` makes oversized resources **more** likely to enter the model. Combined with tools, this is the GitHub-issue pattern generalized: **any** retrieved document can become a tool-use script. OpenAI: don’t fetch arbitrary URLs from tool output. Claude Code: fetched content = injection.

Schema side: GitHub full surface tens of thousands of tokens (§2.2); OpenAPI-naïve “one tool per path+method” on a 400-operation spec is the same failure. Cloudflare `search`/`execute` and Anthropic tool search exist because **inlining the catalog is the bug**.

### 5.6 Tool poisoning and implicit poisoning

Invariant (2025): malicious instructions in `description` (often in `<IMPORTANT>` blocks). User sees “add two numbers”; model reads “send `~/.ssh/id_rsa` as `sidenote`”. Cursor confirmation UI historically hid full args. **Works even if the user never “wanted” that tool** if another poisoned description **shadows** a trusted tool ([TPA](https://invariantlabs.ai/blog/mcp-security-notification)). Spec already says treat descriptions/annotations as untrusted; hosts still inject them into the **system/tools channel**.

MCP-ITP (arxiv 2601.07395, Jan 2026): **implicit** poisoning — the malicious tool is never called; metadata steers the agent to a **privileged** tool. Reported **up to 84.2% ASR** and **0.3% MDR** vs naive detectors on MCPTox / 12 agents ([paper](https://arxiv.org/pdf/2601.07395)). ⚠️ Research ASR, not a production KPI.

**Mitigations:** render full description + schema in HITL; hash-pin catalogs; isolate privilege; MCP-Scan E001–E003 / TF001.

### 5.7 Other production-hard failures

| Mode | Mechanism | Detection |
| --- | --- | --- |
| Mix-up (OAuth) | Evil AS harvests code for honest AS | RFC 9207 `iss` |
| `requestState` forgery | Flip “approved=true” in MRTR blob | AEAD + principal bind |
| `cacheScope: public` leak | Gateway serves User A’s `tools/list` to User B | Never public if filtered by token |
| Header injection | Malicious `x-mcp-header` / `Mcp-Param-*` | Client **MUST** reject bad tools |
| Session assumption on old SDK | Sticky `Mcp-Session-Id` against stateless farm | 4xx/lost elicitation |
| Hosted MCP + ZDR illusion | Data still lands at third party; Anthropic connector ZDR-ineligible | Contract review |
| Sampling deprecation trap | New client drops sampling; old server still asks | Capability: server **MUST NOT** send unsupported `inputRequests` |
| Task poll storms | `pollIntervalMs` ignored | Honor interval; prefer `notifications/tasks` |
| Schema dialect drift | MCP 2020-12 vs OpenAI/Anthropic strict vs Gemini OpenAPI-subset | Cite **03**; validate **after** conversion |
| Registry schema pin | Stale `$schema` on registry entries | VS Code gallery serialization errors (#783) |
| Webhook vs Task split-brain | 2xx webhook but task store write failed | Reconcile from Stripe/GitHub API by stored id |
| At-least-once double charge | Retry `tools/call` without idempotency key | **03** Stripe key in tool args |

---

## 6. Enterprise System Design Scenarios

### 6.1 When to use what (protocol choice)

| Need | Choose | Do not |
| --- | --- | --- |
| CRUD / query / one-shot action with JSON in/out | **MCP tool** or OpenAPI→MCP | A2A task for a calculator |
| Multi-turn negotiation with another org’s agent | **A2A** (Agent Card, artifacts) | Pretend the other agent is a stateless MCP tool |
| Existing REST estate, no agent team | **OpenAPI tool** (Foundry) or AgentCore / Cloudflare OpenAPI wrapper | Hand-write 200 MCP tools on day 1 |
| Mix Search + MCP + OpenAPI under one policy | **Toolbox / MCP gateway PEP** | N direct connections from every agent |
| IDE local files / secrets on laptop | **stdio** + OS sandbox | Remote MCP with env API keys in the cloud host |
| SaaS product consumed by Claude/ChatGPT/Cursor | **Streamable HTTP** + OAuth 2.1 + CIMD | STDIO-only (Claude.ai cannot reach it) |
| Job that outlives the agent process (pay, CI, train) | **Webhook + Tasks dual-path** | Hold Streamable HTTP SSE for 10 minutes through a 60s gateway |

Official A2A line: *MCP inside the agent, A2A between agents* ([A2A and MCP](https://a2a-protocol.org/latest/topics/a2a-and-mcp/)).

### 6.2 Scenario A — Enterprise MCP gateway (PEP)

**Goal:** 50 MCP servers, thousands of tools, per-tenant OAuth, no 55k-token prefix, no page-2 dropouts, one audit stream.

**Topology.** Agents (Cursor / internal runtime / Foundry) speak MCP to **one** gateway URL (`POST /mcp`). Gateway is the PEP: validates Bearer `aud` = gateway canonical URI (RFC 8707); authorizes on `Mcp-Name` + tenant; **does not passthrough** the inbound token; mints outbound OAuth/IAM/API-key per backend (AgentCore dual-auth, Envoy `securityPolicy`, Microsoft router). Backends are OpenAPI-generated tools, first-party MCP (`api.githubcopilot.com/mcp/x/issues/readonly`), and a few stdio adapters on locked-down nodes. `2026-07-28` headers mean the WAF never parses bodies. Round-robin across gateway replicas; **no** session store. Tasks + handles in Redis/DB for the few stateful tools.

**Catalog plane.** Gateway serves a **filtered, deterministically ordered** `tools/list` with `ttlMs` 300000 and `cacheScope: private`. Progressive discovery: two meta-tools (`search_tools`, `invoke_tool`) **or** Anthropic `defer_loading` / OpenAI tool search / Cursor-style names-on-disk. Paginate at a size **every** client in the fleet is known to drain (do not assume 30). Hash the catalog; on `list_changed`, re-prompt HITL for newly added tools.

**Token $ [inferred].** Sonnet 5, 1k agent turns/day, 8.7k live defs cached: ~8.7e3 × 1e3 / 1e6 × $0.20 ≈ **$1.74/day** cache-read for schemas vs ~55k uncached × $2 = **$110/day** if you inline GitHub+Slack+Sentry+Grafana+Splunk every turn. Extra RTT through the gateway is **[inferred]** 5–30 ms; dominated by GitHub/Salesforce p95.

**Security.** EMA for workforce; CIMD for first-party agents; PAT only for break-glass stdio. SSRF allowlist on OAuth discovery. Origin 403. SDK ≥ 1.24.0. PII: no `resources/read` of HR tickets into the same conversation as `send_email`. Sandbox: Cursor Deny-all except `api.github.com` + gateway; VS Code `sandboxEnabled` on remaining stdio.

**Failure drills.** Confused-deputy OAuth test on the proxy path; DNS-rebinding test against any localhost debug port; page-1 client canary; rug-pull hash mismatch; webhook/Task split-brain for the one payment tool.

**Success metrics.** Zero cross-tenant `public` cache hits; 100% `tools/call` have gateway audit rows; schema tokens < 1–5% of context; unknown-tool rate after pagination drain ≈ 0.

### 6.3 Scenario B — SaaS webhook + MCP hybrid for long jobs

**Goal:** a B2B product (payments, CI, video render) consumed by Claude/ChatGPT/Cursor. Sync tools for “create X”; async completion when Stripe/GitHub/Replicate finishes.

**Topology.** Public Streamable HTTP MCP (`2026-07-28`) + OAuth 2.1 (CIMD, PKCE S256, RFC 8707 `resource=https://mcp.example.com`). `create_*` tools that will run long declare Tasks support; if the client lacks the extension, return a handle + `poll_job` tool (document both in the tool description). Separate HTTPS `/webhooks/stripe` behind an event gateway: verify signature, UNIQUE `event.id`, 2xx, enqueue. Worker updates `tasks` row. Optional `notifications/tasks` on listen for still-connected IDEs; **never** rely on it for completion.

**Why not MCP notifications alone.** Listen streams are client-opt-in, proxy-timeout-bound, and gone when Cursor is closed. Stripe retries for **three days**. Those clocks do not match.

**Auth.** Inbound: user OAuth, audience = MCP server. Outbound to Stripe: **Stripe secret key / restricted key on the server**, not the user’s MCP token (passthrough prohibition). URL elicitation if the user must connect their own Stripe account (SEP-1036) — secrets never enter form mode.

**Token $.** Tiny catalog (create/poll/cancel/get_status = four tools). Cost is **result tokens** when poll returns a fat invoice object — truncate / resource_link. OpenAI hosted path: `require_approval: always` on `create_*`; `allowed_tools` ≤ 10; watch **200–2000 RPM**. Anthropic hosted path: tools only; server must be on the public internet from Anthropic IPs; ZDR ineligible.

**Resilience.** Idempotency key in `create_*` args (**03**). Reconciler: if task `working` past SLO, `GET` Stripe/GitHub by stored id. Cooperative cancel: `tasks/cancel` **and** Stripe cancel API; either may win.

**Failure drills.** Duplicate webhook (same `event.id`); webhook 500 then retry with **new** signature; client polls during `input_required` (approval of a $10k payout via form elicitation — amount in the **message**, not a hidden field); client never declared Tasks and drops the handle.

### 6.4 Topology / transport / cost knobs (interview matrices)

| Architecture | Strength | Cost / risk | Fit |
| --- | --- | --- | --- |
| **Direct host→N servers** | Simple; Cursor/VS Code native | Catalog explosion; shadowing; N OAuth dances | <15 trusted servers, developers |
| **Progressive discovery host** | Token + accuracy | Extra meta-tools; cache-bust discipline | 50–1000 tools |
| **Enterprise MCP gateway** | One `aud`, RBAC, audit, `Mcp-Name` WAF, circuit breaking | Extra hop; gateway is a deputy — must not passthrough | Regulated; many teams |
| **Provider-hosted MCP client** (OpenAI/Anthropic) | No local client; multi-server in one API call | Egress to vendor; Anthropic **tools only**; approval UX is theirs; ZDR stops at MCP | Product agents, not VPC-only data |
| **Secure tunnel / Workers** | Private MCP without public IP | Vendor trust; tunnel client ops | On-prem tools for ChatGPT |
| **EMA + IdP** | Central joiner/leaver | Client + AS must implement ID-JAG | Workforce Claude/Cursor at scale |
| **Webhook + Tasks** | Survives 60s gateway timeout and agent restart | Two clocks; split-brain | Payments, CI, render |
| **A2A mesh + MCP leaves** | Org boundaries, long tasks | Two auth stacks; Agent Card sprawl | Supplier/partner agents |

| | stdio | Streamable HTTP + JSON | Streamable HTTP + SSE | Tasks + webhook |
| --- | --- | --- | --- | --- |
| Fan-out | 1 client | Many | Many | Many |
| LB | n/a | Round-robin OK (2026-07-28) | Same; don’t buffer | Poll any replica if task store shared |
| HITL mid-call | MRTR | MRTR | MRTR | `input_required` on task |
| Long job | Blocks process | Proxy timeout | Proxy timeout | **Designed for this** |
| Secrets | env | OAuth | OAuth | OAuth + task ACL + webhook HMAC |
| Cancel | notification | close stream | close stream | `tasks/cancel` + upstream cancel |
| DNS rebinding | n/a | Origin 403 + CVE patch | Same | Webhook is a different origin story (HMAC) |

| Knob | Effect | Citation |
| --- | --- | --- |
| `allowed_tools` / allowlist | Cuts descriptor tokens + attack surface | OpenAI, Claude connector, Cursor |
| Prompt cache + stable ordered `tools/list` | ~10× cheaper descriptor replay (Sonnet $2→$0.20 / MTok) | **01** pricing; MCP tools SHOULD deterministic order |
| Cursor deferred MCP | **−46.9%** agent tokens on MCP-calling runs | Cursor blog; **02** |
| Anthropic tool search | **~55k → ~8.7k** live; **>85%** | Anthropic engineering; **03** |
| Code-mode / OpenAPI search+execute | Spec never enters the LLM | Cloudflare; client best practices |
| `ttlMs` 300000 on catalogs | Fewer `tools/list` RTTs; hash still required for rug-pull | Caching spec |
| Skip HITL (`require_approval: never`) | Lower latency, higher blast radius | OpenAI |
| Tasks vs blocking | Survives 60s gateway timeout | Tasks extension |
| Default GitHub toolsets | 64.6k → 30.3k (maintainer table) | github-mcp-server #1182 |

### 6.5 Interview-ready invariants

1. Host ≠ client ≠ server; one client per server; the LLM never speaks JSON-RPC. Cite **03** for the native-schema dispatcher; this file is the wire.
2. `2026-07-28` is **stateless HTTP**: `_meta` + `Mcp-Method`/`Mcp-Name`; sessions are handles or Tasks; `initialize` is legacy.
3. Transports: stdio (local, env secrets); Streamable HTTP POST-only (2026); GET-SSE sessions (2025, still in OpenAI/Cursor); HTTP+SSE Deprecated (12-month floor).
4. MRTR replaced bidirectional sampling/elicitation streams; `requestState` must be AEAD. Form elicitation **MUST NOT** take secrets; URL mode is third-party OAuth without passthrough.
5. Sampling/roots/logging/HTTP+SSE are **deprecated**, not gone today. Sampling is a confused-deputy-adjacent instruction channel.
6. MCP $ / 1k calls = **$0** protocol + **token** economics (Cursor **−46.9%**; Anthropic **~55k** five-server catalog). ⚠️ No MCP p99.
7. Token **passthrough** is a spec violation; `aud` + RFC 8707 + PKCE S256 are Zero-Trust MCP. PAT/API keys are stdio/outbound only.
8. `nextCursor` is opaque; drain every page; `list_changed` on `subscriptions/listen` invalidates TTL. Page-1 clients are a production outage (**03**).
9. Webhooks (HMAC, 2xx, 3-day retry) and MCP notifications (opt-in SSE) are different planes; long jobs need **both** + Tasks.
10. Tool text is an **instruction channel**; poisoning, shadowing, rug-pull, resource injection, DNS rebinding (CVE-2025-66414), and GitHub-issue deputies are production, not theory. Process isolation ≠ prompt isolation.
11. OpenAPI→MCP (AgentCore, Cloudflare code-mode, Foundry) is the REST on-ramp; the gateway is a PEP that **must** mint new outbound tokens.
12. A2A is the peer plane; MCP is the tool plane; SEPs + registries + gateways are the enterprise control plane.

---

## Sources

1. https://modelcontextprotocol.io/specification/2026-07-28 — protocol authority
2. https://modelcontextprotocol.io/specification/2026-07-28/architecture — host/client/server; per-request `_meta`
3. https://modelcontextprotocol.io/specification/2026-07-28/server/tools — tools/list/call, schema, names, isError, x-mcp-header
4. https://modelcontextprotocol.io/specification/2026-07-28/server/resources — URI resources, subscribe, templates
5. https://modelcontextprotocol.io/specification/2026-07-28/client/elicitation — form vs URL; no secrets in form
6. https://modelcontextprotocol.io/specification/2026-07-28/client/sampling — deprecated; HITL; nested tools
7. https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/stdio — newline JSON-RPC; env credentials
8. https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http — POST-only; Origin; no Last-Event-ID
9. https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization — OAuth 2.1, RFC 8707, RFC 9728, CIMD, DCR deprecated
10. https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/security-considerations — PKCE refuse; mix-up; resource MUST
11. https://modelcontextprotocol.io/specification/2026-07-28/basic/patterns/mrtr — InputRequiredResult; requestState AEAD
12. https://modelcontextprotocol.io/specification/2026-07-28/server/utilities/caching — ttlMs, cacheScope, list_changed invalidation
13. https://modelcontextprotocol.io/specification/2026-07-28/server/utilities/pagination — opaque nextCursor; −32602 invalid cursor
14. https://modelcontextprotocol.io/specification/2026-07-28/changelog.md — sessions gone; GET stream gone; deprecations
15. https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/security_best_practices — confused deputy; passthrough forbidden; SSRF
16. https://modelcontextprotocol.io/docs/2026-07-28/develop/clients/client-best-practices — 1–5% catalog rule; code-mode; progressive discovery
17. https://modelcontextprotocol.io/extensions/tasks/overview — resultType task; poll; cooperative cancel
18. https://modelcontextprotocol.io/extensions/auth/enterprise-managed-authorization — EMA / ID-JAG
19. https://modelcontextprotocol.io/extensions/auth/oauth-client-credentials — SEP-1046 M2M
20. https://modelcontextprotocol.io/registry/about — official registry; not a malware scanner
21. https://blog.modelcontextprotocol.io/posts/2026-07-28/ — stateless core; Honeycomb 20%; SDK 0.5B/mo; partner quotes
22. https://blog.modelcontextprotocol.io/posts/2025-09-08-mcp-registry-preview/ — registry preview
23. https://www.jsonrpc.org/specification — JSON-RPC 2.0
24. https://datatracker.ietf.org/doc/html/rfc8707 — Resource Indicators
25. https://www.rfc-editor.org/rfc/rfc8707.html — RFC 8707 HTML
26. https://datatracker.ietf.org/doc/html/rfc9728 — Protected Resource Metadata
27. https://datatracker.ietf.org/doc/html/rfc9207 — authorization server `iss`
28. https://datatracker.ietf.org/doc/html/draft-ietf-oauth-v2-1-13 — OAuth 2.1
29. https://datatracker.ietf.org/doc/html/draft-ietf-oauth-client-id-metadata-document-00 — CIMD
30. https://www.rfc-editor.org/rfc/rfc9068.html — JWT access-token `aud`
31. https://modelcontextprotocol.io/specification/2025-11-25/basic/transports — GET SSE + sessions (still in the wild)
32. https://modelcontextprotocol.io/specification/2025-03-26/basic/transports — Streamable HTTP introduction
33. https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization — passthrough language; RFC 8707 MUST
34. https://mcp.mintlify.app/community/sep-guidelines — SEP process; conformance for Final
35. https://modelcontextprotocol.org/seps/1036-url-mode-elicitation-for-secure-out-of-band-intera — URL elicitation vs passthrough
36. https://github.com/advisories/GHSA-w48q-cv73-mx4w — CVE-2025-66414
37. https://github.com/modelcontextprotocol/typescript-sdk/security/advisories/GHSA-w48q-cv73-mx4w — TS SDK DNS rebinding advisory
38. https://osv.dev/vulnerability/GHSA-w48q-cv73-mx4w — OSV record; fixed 1.24.0
39. https://github.com/modelcontextprotocol/typescript-sdk/blob/e4227d13/packages/middleware/express/src/middleware/hostHeaderValidation.ts — hostHeaderValidation
40. https://platform.claude.com/docs/en/agents-and-tools/mcp-connector — hosted MCP client; tools only; ZDR ineligible
41. https://platform.claude.com/docs/en/managed-agents/mcp-connector — 20-server cap; mcp_toolset pairing
42. https://www.anthropic.com/engineering/advanced-tool-use — 58 tools ≈ 55k; tool search ~8.7k
43. https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool — defer_loading; >85%; 3–5 tools
44. https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp — Anthropic egress IPs
45. https://claude.com/docs/connectors/overview — connectors directory
46. https://code.claude.com/docs/en/mcp — Claude Code MCP; SSE deprecated warning
47. https://github.com/anthropics/claude-code/issues/20307 — Platform docs missing SSE deprecation
48. https://github.com/anthropics/claude-code/issues/39586 — page-1-only tools/list
49. https://developers.openai.com/api/docs/guides/tools-connectors-mcp — $0 per call; RPM; approval; tunnel
50. https://developers.openai.com/api/docs/guides/tools-tool-search — progressive tool load
51. https://cursor.com/docs/mcp — transports; OAuth redirects; sandbox; isolation
52. https://cursor.com/blog/dynamic-context-discovery — −46.9% MCP-calling runs
53. https://forum.cursor.com/t/what-do-your-attached-mcp-servers-actually-cost-you-in-tokens-per-request-i-measured-it/166405 — Cursor staff correction
54. https://code.visualstudio.com/docs/copilot/customization/mcp-servers — VS Code MCP host
55. https://code.visualstudio.com/docs/agents/reference/mcp-configuration — sandboxEnabled; not Windows
56. https://github.com/github/github-mcp-server — official GitHub MCP; toolsets
57. https://github.com/github/github-mcp-server/discussions/1182 — 101/64.6k → 52/30.3k
58. https://github.com/github/github-mcp-server/blob/main/docs/remote-server.md — api.githubcopilot.com/mcp URLs
59. https://github.com/PavelTkachenk0/ContextTax — count_tokens 10,928 / 20,404
60. https://getunblocked.com/blog/github-mcp-token-cost/ — community 55k/93-tool snapshot
61. https://github.com/awslabs/agentcore-samples/tree/main/06-workshops/02-AgentCore-gateway/02-transform-apis-into-mcp-tools — OpenAPI→MCP dual auth
62. https://github.com/aws/agentcore-cli/blob/main/docs/gateway.md — target types; outbound oauth/api-key
63. https://catalog.workshops.aws/strands/en-US/module-8-amazon-bedrock-agentcore/8-4-agentcore-gateway/8-4-2-openapi-to-mcp-tools — gateway URL shape
64. https://blog.cloudflare.com/mcp-v2/ — Workers stateless MCP
65. https://developers.cloudflare.com/agents/model-context-protocol/apis/handler-api/ — keepAliveMs 15s; maxSubscriptions 1024
66. https://developers.cloudflare.com/agents/model-context-protocol/guides/build-codemode-openapi-mcp-server/ — search+execute OpenAPI
67. https://developers.cloudflare.com/agents/model-context-protocol/guides/migrate-to-mcp-sdk-v2/ — Origin 403; per-request factory
68. https://github.com/Cloudflare/mcp-server-cloudflare — /sse 410; dual-speak 2025/2026
69. https://github.com/microsoft/mcp-gateway — K8s MCP reverse proxy
70. https://microsoft.github.io/mcp-gateway/ — POST /mcp router
71. https://aigateway.envoyproxy.io/docs/capabilities/mcp/ — MCPRoute; toolSelector
72. https://github.com/envoyproxy/ai-gateway/blob/main/examples/mcp/mcp_example.yaml — includeRegex example
73. https://learn.microsoft.com/en-us/azure/ai-foundry/agents/concepts/tool-catalog — MCP vs OpenAPI vs A2A vs Toolbox
74. https://a2a-protocol.org/latest/ — A2A home
75. https://a2a-protocol.org/latest/topics/a2a-and-mcp/ — complementary planes
76. https://docs.stripe.com/webhooks — 2xx; 3-day retry; Event object
77. https://docs.stripe.com/webhooks/signatures — Stripe-Signature t=,v1=
78. https://docs.stripe.com/webhooks/quickstart — raw body; 400 on bad sig
79. https://hookdeck.com/blog/mcp-event-gateway — event gateway in front of MCP
80. https://workos.com/blog/what-is-mcp-authorization — 2026-07-28 auth walkthrough
81. https://invariantlabs.ai/blog/mcp-security-notification — tool poisoning / shadowing
82. https://invariantlabs.ai/blog/mcp-github-vulnerability — toxic flow via public issue
83. https://invariantlabs.ai/blog/introducing-mcp-scan — tool pinning / hash
84. https://github.com/invariantlabs-ai/mcp-injection-experiments — direct poison + rug-pull demos
85. https://arxiv.org/pdf/2601.07395 — MCP-ITP implicit poisoning ASR
86. https://csrc.nist.gov/pubs/sp/800/207/final — Zero Trust architecture
87. https://github.com/modelcontextprotocol/registry — registry source
88. https://registry.modelcontextprotocol.io — canonical metadata API
89. https://github.com/modelcontextprotocol/registry/issues/783 — $schema drift vs VS Code
90. https://github.com/modelcontextprotocol/typescript-sdk/blob/main/docs/servers/notifications.md — list_changed on listen streams
91. https://py.sdk.modelcontextprotocol.io/advanced/pagination/ — drain until next_cursor is None
92. https://policylayer.com/attacks/token-mis-redemption — why RFC 8707 is MUST
93. https://gofastmcp.com/integrations/anthropic.md — mcp_toolset pairing rule
94. https://docs.github.com/en/copilot/how-tos/provide-context/use-mcp-in-your-ide/extend-copilot-chat-with-mcp — Copilot MCP gallery
95. https://modelcontextprotocol.io/seps/1024-mcp-client-security-requirements-for-local-server- — SEP-1024 local install
96. https://www.zenml.io/llmops-database/dynamic-context-discovery-for-production-coding-agents — Cursor DCD write-up; auth metadata on files
