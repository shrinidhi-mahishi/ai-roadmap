# Module 10 — MCP & Interoperability

**Audience**: personal study and enterprise interview prep.
**Grounded in**: `research_cursor_grok/research/10-mcp-interoperability.md` (researched 2026-08-21, 67 sources). Protocol authority: `2026-07-28` at modelcontextprotocol.io. Prices are vendor token/tool rates as of 2026-08-21, **not** MCP-protocol fees.
**Mandatory topics**: Tools · Resources · MCP servers · MCP clients · Interoperability.

The unit of production is not “we added an MCP server.” It is a **control plane** that authenticates every request, negotiates capabilities, allowlists tools, and routes on `Mcp-Method` / `Mcp-Name` **without parsing the body**, wrapping a **data plane** that runs `tools/call`, `resources/read`, MRTR elicitation, and Task poll/result. MCP is a **three-role** topology: host, client, server. **The model never speaks MCP.** It emits a native function call; a client inside the host translates to JSON-RPC. Interview answers that skip this split fail when the follow-up is “who is the OAuth resource server, and why must that token never be forwarded upstream?”

**Invariant:** one client ↔ one server; hosts instantiate **independent** clients. Process isolation is not prompt isolation — cross-server context is shared and is the shadowing surface.

---

## 1. System Topology & Data Flow

### 1.1 Topology

Control plane owns identity (OAuth 2.1 / EMA / CIMD+PKCE), RFC 9728 protected-resource metadata, per-request `_meta` (`protocolVersion`, `clientInfo`, `clientCapabilities`), optional cacheable `server/discover`, gateway policy, `subscriptions/listen` catalog change, and extension negotiation (`io.modelcontextprotocol/tasks`, EMA, MCP Apps). `2026-07-28` retired `initialize`/`initialized` and `Mcp-Session-Id` (SEP-2575, SEP-2567): the control plane is **stateless**; every request is self-describing. Application state that used to hide in the transport **must** be an **explicit handle** in tool arguments.

Data plane owns `tools/call`, `resources/read`, `content` / `structuredContent`, resource blobs, elicitation payloads, and Task poll/result. Persistence is **handles + Tasks + MRTR `requestState`**, not a session cookie. Tool proxies are **upstream APIs** reached with a **new** token (on-behalf-of / client-credentials / workload identity) — never the inbound MCP access token. Telemetry is the only authoritative place for `Mcp-Method`/`Mcp-Name` access logs, OTel `traceparent` in `_meta` (SEP-414), hashed args, and policy decisions. The protocol logging utility is **deprecated**; use stderr (stdio) or OpenTelemetry.

Transports: **stdio** (host-spawned subprocess, newline-delimited JSON-RPC on stdin/stdout; stderr = logs only) or **Streamable HTTP** (one POST endpoint; `Accept: application/json, text/event-stream`; JSON object **or** SSE scoped to **that** request). Deprecated HTTP+SSE (2024-11-05) has a 12-month minimum offramp; Cursor still documents `SSE` for older servers; OpenAI Responses still accepts Streamable HTTP **or** HTTP/SSE.

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│ HOST  (Claude Desktop / Claude.ai / Cursor / VS Code / ChatGPT / custom runtime)│
│ UX · consent UI · tool-approval policy · LLM conversation · multi-server orch.  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌─────────────────────┐  │
│  │ Client A     │  │ Client B     │  │ Client C     │  │ Native tool schema  │  │
│  │ 1:1 Server A │  │ 1:1 Server B │  │ 1:1 Server C │  │ map tools/list →    │  │
│  │ stdio | HTTP │  │ crash-isol.  │  │ prefix names │  │ model function JSON │  │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────────┬──────────┘  │
└─────────┼─────────────────┼─────────────────┼─────────────────────┼─────────────┘
          │ JSON-RPC 2.0    │                 │                     │
          │ _meta + headers │                 │                     │ LLM never
          ▼                 ▼                 ▼                     │ speaks MCP
┌─────────┴─────────────────┴─────────────────┴─────────────────────┴─────────────┐
│ CONTROL PLANE  (host policy + IdP + API/MCP gateway)                            │
│  ┌────────────┐  ┌──────────────┐  ┌──────────────┐  ┌───────────────────────┐  │
│  │ Gateway    │─▶│ OAuth 2.1    │─▶│ Discover /   │─▶│ Allowlist + HITL      │  │
│  │ Mcp-Method │  │ RFC 9728 PRM │  │ capability   │  │ Cursor/Claude/OpenAI  │  │
│  │ Mcp-Name   │  │ RFC 8707 aud │  │ ttlMs cache  │  │ allowed_tools; pin    │  │
│  │ MCP-Proto- │  │ no passthru  │  │ list_changed │  │ catalog hash          │  │
│  │ Version    │  │ CIMD / EMA   │  │              │  │                       │  │
│  │ Origin 403 │  │ PKCE S256    │  │              │  │                       │  │
│  └─────┬──────┘  └──────┬───────┘  └──────┬───────┘  └───────────┬───────────┘  │
│        │                │                 │                      │              │
│        │  POST /mcp  or stdio NDJSON      │  subscriptions/listen│              │
└────────┼────────────────┼─────────────────┼──────────────────────┼──────────────┘
         │                │                 │                      │
         ▼                ▼                 ▼                      ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│ DATA PLANE — MCP SERVERS                                                        │
│  ┌─────────────────────┐  ┌─────────────────────┐  ┌──────────────────────────┐ │
│  │ stdio subprocess    │  │ Streamable HTTP     │  │ Tools / Resources        │ │
│  │ stdin/stdout JSON   │  │ POST; SSE per req   │  │ tools/list|call          │ │
│  │ cancel=notification │  │ cancel=close SSE    │  │ resources/list|read      │ │
│  │ SIGTERM→SIGKILL     │  │ X-Accel-Buffering   │  │ templates; subscribe     │ │
│  │ bind n/a            │  │ Origin; 127.0.0.1   │  │ MRTR input_required      │ │
│  └──────────┬──────────┘  └──────────┬──────────┘  └────────────┬─────────────┘ │
│             │                        │                          │               │
│             └────────────┬───────────┴──────────────────────────┘               │
│                          ▼                                                      │
│             ┌────────────────────────────────────────┐                          │
│             │ TOOL PROXIES (upstream APIs)           │                          │
│             │ NEW token (OBO / CC / workload id)     │                          │
│             │ NEVER inbound MCP Bearer               │                          │
│             │ schema validate; sanitize outputs      │                          │
│             └────────────────────┬───────────────────┘                          │
└──────────────────────────────────┼──────────────────────────────────────────────┘
                                   │
┌──────────────────────────────────┼──────────────────────────────────────────────┐
│ PERSISTENCE                      ▼                                              │
│  ┌──────────────────┐  ┌──────────────────┐  ┌───────────────────────────────┐  │
│  │ Explicit handles │  │ Tasks extension  │  │ MRTR requestState             │  │
│  │ cart/browser id  │  │ taskId + ttlMs   │  │ HMAC/AEAD; principal+TTL bind │  │
│  │ authz re-check   │  │ poll any replica │  │ single-use nonce store        │  │
│  │ unauth = bearer  │  │ cooperative cxl  │  │ attacker-controlled blob      │  │
│  └──────────────────┘  └──────────────────┘  └───────────────────────────────┘  │
│  Soft: tools/list ttlMs + cacheScope (never public if token-filtered)           │
└──────────────────────────────────┬──────────────────────────────────────────────┘
                                   │
┌──────────────────────────────────┴──────────────────────────────────────────────┐
│ TELEMETRY / OBSERVABILITY SINKS                                                 │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────┐  ┌───────────────────────┐  │
│  │ WORM audit  │  │ Metrics      │  │ Traces      │  │ Usage (LLM tokens;    │  │
│  │ Mcp-Name,   │  │ breaker,     │  │ OTel        │  │ MCP SKU = $0)         │  │
│  │ hashed args,│  │ RPM 200–2000,│  │ traceparent │  │ descriptor vs result  │  │
│  │ policy,     │  │ listen idle  │  │ in _meta    │  │                       │  │
│  │ catalog hash│  │              │  │             │  │                       │  │
│  └─────────────┘  └──────────────┘  └─────────────┘  └───────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

**Inverted topology (hosted connectors).** Anthropic Messages MCP connector and OpenAI Responses `type: "mcp"` make the **provider** the MCP client. Your app never opens a socket to the server. Anthropic remote connectors egress from **Anthropic IPs** (not the laptop); tools-only; not on Bedrock/Vertex. OpenAI: `server_url` **or** `connector_id` **or** `tunnel_id` (Secure MCP Tunnel). Private-VPC MCP **will not** work for claude.ai.

### 1.2 End-to-end request flow

1. **Ingress.** User talks to the **host**. Gateway (or the host itself) stamps a correlation id. HTTP: validate `Origin` or 403 (DNS rebinding); local servers bind `127.0.0.1` not `0.0.0.0`.
2. **Identity.** Unauth HTTP → `401 WWW-Authenticate: Bearer resource_metadata=…, scope=…` → PRM → AS metadata → CIMD or static/DCR → PKCE S256 → authorize with `resource` = MCP server URI → validate `iss` (RFC 9207 / SEP-2468) → Bearer. STDIO **SHOULD NOT** use this profile — credentials from the environment. EMA: employee SSO to the host; IdP issues **ID-JAG**; MCP AS exchanges it for an MCP access token.
3. **Capability.** Every request **MUST** carry `_meta.io.modelcontextprotocol/protocolVersion` and **SHOULD** carry `clientInfo` + `clientCapabilities`. Probe `server/discover` (cacheable `ttlMs`, `cacheScope`). On `DiscoverResult` stay modern; on `UnsupportedProtocolVersionError` pick from `supported`; on timeout/other **then** legacy `initialize` — do not key fallback on one error code.
4. **Catalog.** Client `tools/list` (paginated, deterministically ordered). Host allowlist / `allowed_tools` / Claude `mcp_toolset` deny. Prefix with a **client-assigned** server id, not `serverInfo.name`. Hash the list; `list_changed` = re-review (rug-pull).
5. **Plan.** Host packs native tools for the LLM. Progressive discovery if defs exceed **~1–5%** of the context window (`search_tools` → `get_tool_details` → execute). The model emits a native call; it never sees JSON-RPC `id`s.
6. **Dispatch (data plane).** Client `tools/call` with a new JSON-RPC `id`. HTTP headers: `MCP-Protocol-Version`, `Mcp-Method`, `Mcp-Name` (SEP-2243) so the gateway routes without a body parse. Optional `Mcp-Param-*` from `x-mcp-header` primitives — **MUST NOT** put secrets/PII there; drop tools that violate RFC 9110 `tchar`.
7. **Execute.** Server validates schema (JSON Schema 2020-12 default), re-checks authz on handles, calls upstream with a **new** token. Business failure → success envelope with `isError: true` (model can self-correct). Protocol failure → JSON-RPC `-32602` / `-32603` (clients **SHOULD** also accept legacy `-32002` for missing resources).
8. **MRTR (if needed).** `resultType: "input_required"` + `inputRequests{}` + opaque `requestState`. Client gathers form/url elicitation (hosts **MUST** show **which server** and allow decline). Retry the **same method** with a **new** id, echo `requestState`. Treat `requestState` as attacker-controlled: HMAC/AEAD, bind principal+TTL+method/args; single-use = server nonce store.
9. **Long work.** `resultType: "task"` + `taskId` / `ttlMs` / `pollIntervalMs` → `tasks/get` until `completed|failed|cancelled`. Do not hold HTTP for CI/batch. Persist `taskId`; poll any replica if the task store is shared.
10. **Resources (application-driven).** Host picker/search/auto-attach → `resources/read`. Missing URI → `-32602`, not empty `contents[]`. Sanitize `file://` (traversal). Subscriptions: `subscriptions/listen` + `notifications/resources/updated` correlated by `_meta` `subscriptionId`. SSE keep-alives (`:` lines) + `X-Accel-Buffering: no`. **No** `Last-Event-ID` resume in this revision — reconnect replays from app handles / Tasks.
11. **Emit and audit.** Host injects tool/resource bytes as **untrusted** (delimiter; JSON-encode). WORM log: method, name, hashed args, policy, catalog hash. Terminal LLM usage event is the token bill; MCP itself has **no** settlement layer.

**Interview talking point:** “The LLM is an untrusted planner. MCP is JSON-RPC to a resource server. IAM, egress, and upstream credentials live on the server/gateway. `2026-07-28` deleted sessions; if you still key on `Mcp-Session-Id` you will lose elicitation on the first round-robin hop.”

### 1.3 MCP clients in the market (hosts vs connectors)

| Host | Transport | What it is | Notes |
| --- | --- | --- | --- |
| **Cursor** | stdio, Streamable HTTP, legacy SSE | IDE host; one client per `mcp.json` | OAuth loopback `http://localhost:8787/callback` + `https://www.cursor.com/agents/mcp/oauth/callback`; static `auth.CLIENT_ID`; enterprise allowlist + per-server network sandbox; one server crash does not take others down |
| **Claude.ai / Desktop** | Remote: Anthropic-brokered HTTP; Desktop also stdio | Host; remote egress = **Anthropic IPs** | Directory + verification labels; each user still OAuth’s |
| **Claude API MCP connector** | Streamable HTTP or SSE; **tools only** | Anthropic **is** the client (`mcp-client-2025-11-20`) | No STDIO; `mcp_servers[]` + `tools: [{type:"mcp_toolset"}]`; allow/deny |
| **OpenAI Responses / ChatGPT** | Streamable HTTP or HTTP/SSE | OpenAI **is** the client | `require_approval` default-on; `allowed_tools`; `mcp_list_tools` cached in context; **no extra $ per MCP call**; RPM 200/1000/2000 by tier |
| **VS Code Copilot** | stdio / `type: http` | Host; gallery + `.vscode/mcp.json` | macOS/Linux stdio sandbox (**not Windows**); resources via Add Context |

---

## 2. Core Mechanics & Algorithms

### 2.1 JSON-RPC 2.0 contract

JSON-RPC 2.0 is the message contract. Requests: `id` + `method` + `params`. Notifications: **no** `id`. Protocol errors use JSON-RPC codes (`-32602` invalid params / unknown tool / resource not found; `-32603` internal). Tool **business** failures are **not** JSON-RPC errors: they are successful results with `isError: true`.

HTTP vs stdio cancellation is a **different algorithm**: stdio sends `notifications/cancelled`; Streamable HTTP **closes the SSE stream** and MUST NOT POST `notifications/cancelled`. Shutdown stdio: close stdin, wait, SIGTERM→SIGKILL (POSIX) or `TerminateProcess`/Job Objects (Windows). Unexpected death: client **SHOULD** restart; in-flight calls are lost; re-open `subscriptions/listen`.

**Complexity.** Method dispatch is \(O(1)\) on `method`. Gateway routing on `Mcp-Name` is \(O(1)\) header match — SEP-2243 exists so Envoy/Cloudflare/Microsoft gateways **never parse JSON bodies**. Catalog size \(n\) tools costs \(\Theta(n)\) descriptor tokens **per LLM turn** until prompt cache hits; that, not JSON-RPC framing, is the hot path.

### 2.2 Tools (model-controlled)

Servers declaring `tools` **MUST** implement `tools/list` + `tools/call`. Lists are paginated, cacheable, and **SHOULD** be **deterministically ordered** (SEP-2549) to stabilize LLM prompt caches. Tool lists **MUST NOT** vary as a side effect of other requests on a connection; they **MAY** vary by **authorization presented on that request**.

| Rule | Spec |
| --- | --- |
| `inputSchema` | JSON Schema **object**; default dialect **2020-12** if `$schema` omitted (SEP-1613, SEP-2106) |
| `outputSchema` | If present, `structuredContent` **MUST** conform; clients **SHOULD** validate |
| Dual-write | Structured results **SHOULD** also appear as JSON `text` for older hosts |
| Parameterless | `{ "type": "object", "additionalProperties": false }` |
| Names | 1–128 chars; `[A-Za-z0-9_.-]`; case-sensitive; unique **per server** |
| Aggregator prefix | Client-assigned server id, **not** `serverInfo.name` |
| `resultType` | `"complete"` (normal / `isError`); `"input_required"` (MRTR); `"task"` if Tasks |
| Content | `text`, `image`, `audio`, `resource_link`, embedded `resource` |
| Annotations | **Untrusted** unless the server is trusted. HITL: hosts **SHOULD** confirm; tools = arbitrary execution |
| Sampling | Deprecated as of `2026-07-28` (12-month clock). New work: server calls the vendor LLM, or host uses code-mode |

**Handle rules (state that survived session deletion):** authenticated handle is a **name**, not a capability — re-check authz every call. Unauthenticated handle **is** a bearer token → UUIDv4-class entropy + TTL. Opaque; document lifetime on the **create** tool. Unknown/expired → `isError: true` with a recoverable message.

**Code-mode.** Host compiles MCP schemas to sandbox functions; only `console.log` / summary returns to the model. Sandbox has **no** network; host brokers `tools/call` and retains credentials.

### 2.3 Resources (application-driven)

Resources are URI-identified context (RFC 3986), **not** actions. Hosts choose UX: picker, search, auto-attach. Methods: `resources/list`, `resources/read`, `resources/templates/list`. Contents: `text` or base64 `blob`. `resources/read` **MAY** return multiple contents (directory) and **MAY** return `InputRequiredResult` before a sensitive read.

Templates: RFC 6570 URI templates; arguments can use the completion utility. Annotations `audience` (`user`|`assistant`), `priority` 0.0–1.0, `lastModified` — hints for stuffing the model; **never** an authz signal. `https://` URIs **SHOULD** be fetchable by the client directly; otherwise `file://`, `git://`, or custom. Servers **MUST** sanitize `file://` paths.

**Subscriptions.** If `resources.subscribe`, client opens `subscriptions/listen` with `resourceSubscriptions` URIs; server emits `notifications/resources/updated` with `subscriptionId`. `listChanged` → `notifications/resources/list_changed` on the listen stream. `2026-07-28` moved change notifications **off** GET SSE onto this opt-in listen stream.

Resource bytes are **untrusted text** with the same priority as a user doc. Combined with tools this is the GitHub-issue confused-deputy pattern generalized: **any** retrieved document can become a tool-use script. Spec `audience: ["assistant"]` makes injection **more** likely.

### 2.4 Capability negotiation (per request, not per session)

```
                    ┌─ DiscoverResult ──────────────────────▶ modern 2026-07-28
probe server/discover ─ UnsupportedProtocolVersionError ──▶ pick from supported[]
                    └─ timeout / other error ──────────────▶ THEN legacy initialize
```

Client capabilities that matter in 2026:

- `elicitation.form` / `elicitation.url` — empty `elicitation: {}` ≡ form-only (compat).
- `sampling` / `sampling.tools` — **deprecated**; still on the 12-month clock.
- `extensions`: `io.modelcontextprotocol/tasks`, `io.modelcontextprotocol/enterprise-managed-authorization`, MCP Apps.

Server capabilities: `tools.listChanged`, `resources.listChanged` / `resources.subscribe`, `prompts`, extensions. Server **MUST NOT** send unsupported `inputRequests` (sampling deprecation trap).

**Caching.** `ttlMs` + `cacheScope`. Spec example `ttlMs: 300000` (5 min) cuts MCP refetch, which is cheap vs LLM tokens but matters for hosted-MCP first-byte. Serve **stale** on refetch error (spec allows). `list_changed` **immediately** invalidates even inside TTL. `public` lists may be shared across tokens — **never** mark per-user catalogs `public`. Paginated lists: per-page TTL, no snapshot guarantee; invalid cursor → drop all pages.

### 2.5 OAuth 2.1 (HTTP only) — algorithm

**Stack:** OAuth 2.1 draft-13, RFC 6750 Bearer, RFC 8414 AS metadata **or** OIDC Discovery, RFC 9728 PRM (**MUST** on MCP servers), RFC 8707 `resource` (**MUST** on clients), RFC 9207 `iss` on the auth response, CIMD **SHOULD**; RFC 7591 DCR **deprecated**, retained for compat.

```
unauth POST /mcp
  → 401 + WWW-Authenticate (resource_metadata, scope)
  → GET PRM (RFC 9728)
  → AS metadata / OIDC
  → CIMD (HTTPS client_id URL) or static / DCR
  → PKCE S256 (refuse if code_challenge_methods_supported absent)
  → authorize(resource = MCP server URI)
  → validate iss
  → token(resource) → Bearer to MCP
```

Scope strategy: challenge `scope` is authoritative for **this** operation; step-up rather than asking `scopes_supported` maximally. Bind client credentials to the issuing AS (SEP-2352). `application_type` on DCR so localhost redirects work for CLI (SEP-837). CIMD makes the **AS** an SSRF client when fetching client metadata.

Cursor: RFC 8252 loopback; register **both** redirect URIs; `mcp.json` `auth` only `CLIENT_ID` / `CLIENT_SECRET` / `scopes` — no `redirect_uri` field.

**Token-passthrough is forbidden.** MCP servers **MUST** accept only tokens **audienced to themselves** and **MUST NOT** forward the inbound access token to upstream APIs.

### 2.6 MRTR and Tasks — state machines

**MRTR (SEP-2322)** is the **only** legal way for a server to ask the client for elicitation, sampling, or roots in `2026-07-28` — a breaking change from bidirectional SSE requests.

```
tools/call | resources/read | prompts/get
        │
        ├─ resultType=complete ──▶ isError? model self-correct : done
        ├─ resultType=input_required ──▶ client elicitation (form|url)
        │         │                      echo requestState, NEW json-rpc id
        │         └────────────────────── retry SAME method
        └─ resultType=task ──▶ persist taskId
                  │            tasks/get until completed|failed|cancelled
                  └─ mid-flight input_required ──▶ tasks/update
```

**Elicitation:** **form** = restricted flat JSON Schema; data **is** visible to the client (and often the model); **MUST NOT** collect passwords, API keys, tokens, payment credentials. **url** = out-of-band; secrets **never** transit the MCP client (SEP-1036).

**Tasks (`io.modelcontextprotocol/tasks`, SEP-1686 / SEP-2663):** `taskId`, `ttlMs`, `pollIntervalMs`; optional `notifications/tasks` on listen. Cancellation is **cooperative**. Crash resilience = persist `taskId` and poll after reconnect — this is the durable-work algorithm MCP actually specifies.

### 2.7 A2A vs MCP (interoperability)

Official A2A line: **MCP = agent→tool/resource; A2A = agent→agent** (opaque peers, Agent Cards, task lifecycle). Complementary; A2A is not a tool-call protocol and not an ADK. Pattern: Shop Manager talks to customer/supplier via A2A; Mechanic uses MCP for scanner/manual/lift. An A2A skill **MAY** be re-exposed as a **stateless** MCP tool; do **not** flatten multi-turn agent work into `tools/call`.

**OpenAPI.** Native LLM tools are JSON Schema (OpenAI/Anthropic) or OpenAPI-subset (Gemini). MCP `inputSchema` is JSON Schema 2020-12. Production adapter: OpenAPI 3.0/3.1 operation → one MCP tool; auth stays **outside** the model. Foundry catalogs MCP, OpenAPI, **and** A2A as distinct tool types, plus a **Toolbox** that fronts them as one MCP endpoint.

**Gateways (control-plane multiplexers):** Microsoft MCP Gateway = K8s reverse proxy + tool registry + `POST /mcp` router. Envoy AI Gateway 1.0 `MCPRoute` multiplexes servers with include/exclude tool filters and steers around rate-limited backends. Cloudflare: stateless MCP on Workers; `Mcp-Method`/`Mcp-Name` routing; Durable Objects only when coordinated state is required.

**Registries.** Official metadata store: `registry.modelcontextprotocol.io`. Reverse-DNS names bound by GitHub OAuth/OIDC, DNS, or HTTP proof. **Not** a malware scanner — scanning is delegated to npm/PyPI/Docker and aggregators. Preview: breaking changes possible. CVE-2026-44427: open redirect in trailing-slash middleware (fixed ≥1.7.5). Anthropic Connectors Directory and Cursor Marketplace are **aggregators**, not the protocol registry. Registry namespace auth stops **name squatting**, not **post-publish rug-pull**.

### 2.8 Invariants (interview-ready)

1. Host ≠ client ≠ server; one client per server; the LLM never speaks JSON-RPC.
2. `2026-07-28` is **stateless HTTP**: `_meta` + `Mcp-Method`/`Mcp-Name`; sessions are handles or Tasks.
3. MRTR replaced bidirectional sampling/elicitation streams; `requestState` must be AEAD.
4. Sampling / roots / logging / HTTP+SSE are **deprecated** (12-month floor), not gone today.
5. MCP $ / 1k calls = **$0** protocol + **token** economics; Web Search is a **$10/1k** SKU. ⚠️ No MCP p99.
6. Token **passthrough** is a spec violation; `aud` + RFC 8707 are Zero-Trust MCP.
7. Tool text is an **instruction channel**; poisoning, shadowing, rug-pull, resource injection are production.
8. A2A is the peer plane; MCP is the tool plane; gateways/registries are the enterprise control plane.
9. Annotations, `cacheScope`, and first-party UIs that hide arguments will lie to humans.
10. Multi-server **isolation of process** ≠ **isolation of prompt**.

---

## 3. Token Economics & NFR Analysis

MCP has **no** settlement layer. OpenAI: *“When you’re using the MCP tool, you only pay for tokens used when importing tool definitions or making tool calls. There are no additional fees involved per tool call.”* Contrast a **metered hosted tool** on the same API: Web search **$10.00 / 1k calls** plus search-content tokens (Anthropic web search **$10 / 1K searches**, excluding request-processing tokens). If schemas and result sizes are equal, MCP vs native function calling is the **same token class**. Hosted MCP adds provider-side `tools/list` + remote RTT, still billed as tokens, not as a per-call SKU.

### 3.1 Cost per 1k runs

Published model rates that dominate MCP cost (2026-08-21, vendor pages — not MCP):

| Model | Input / MTok | Cached input / MTok | Output / MTok |
| --- | --- | --- | --- |
| OpenAI `gpt-5.6-sol` (alias `gpt-5.6`) | $5.00 | $0.50 | $30.00 |
| Anthropic Opus 5 | $5.00 | $0.50 | $25.00 |
| Anthropic Sonnet 5 | $2.00 | $0.20 | $10.00 |
| Anthropic Haiku 4.5 | $1.00 | $0.10 | $5.00 |
| Anthropic Fable 5 | $10.00 | $1.00 | $50.00 |

OpenAI Fast mode for Sol: **2×** token price, **99.9%** uptime SLA, latency SLA **99% of 5-minute windows with p50 > 80 output tokens/s** — this is the **LLM plane**, not MCP RTT. Anthropic: US-only inference **1.1×**; Opus 5 fast mode **2×** for ~2.5× speed.

Worked **[inferred]** illustration (research §2.3; not a benchmark). Assume 80 tools × 350 tokens/definition ≈ **28k descriptor tokens**.

| Path | Uncached input $ (Sol / Opus 5) | Cache-read $ | Notes |
| --- | --- | --- | --- |
| 28k descriptors / turn | \(28\mathrm{k}/10^6 \times \$5 = \$0.140\) | \(\times \$0.50 = \$0.014\) | Paid **every turn** if `tools` sits in a **stable** cached prefix |
| 1k `tools/call` results @ 800 tokens through the model | \(800\mathrm{k} \times \$5/10^6 = \$4.00\) input + output | n/a | Code-mode/sandbox filters this **out** of the LLM |
| Same 1k calls, MCP protocol fee | **$0** | — | OpenAI statement |
| **[inferred]** 1k calls, Sol, 400-token args+result, schemas cached | \(\approx 0.4\,\mathrm{MTok} \times \$5 = \$2.00\) + output | — | vs **$10.00** if those 1k were Web Search SKUs |

Prompt-cache interaction: adding/removing tools mid-conversation **invalidates** the prefix; a miss can cost more than the tools you dropped. Mitigations: deterministic `tools/list` order; append new defs after the cache breakpoint; or a single stable `call_tool({name,args})` meta-tool; disconnect servers at **conversation boundaries**, not per turn. Stable ordered lists: ~**10×** cheaper descriptor replay (Sol $5→$0.50 / MTok).

MCP loses when catalogs are **uncached and huge**, or when hosted MCP `tools/list` runs **every** new conversation without `mcp_list_tools` reuse. OpenAI: servers with **dozens** of tools cause “high cost and latency”; use `allowed_tools`. Official client guidance: if tool definitions exceed **~1–5%** of the context window, switch to progressive discovery or vendor tool-search.

### 3.2 Latency SLA targets and mitigations

MCP spec defines error mapping, **not** 99.9%. ⚠️ **No** published MCP `tools/call` p50/p95/p99. Dominated by upstream API + model round-trip, not JSON-RPC framing.

**[inferred]** latency budget for a remote `tools/call` (order-of-magnitude, **not** an SLO): TLS+auth 20–80 ms; JSON-RPC 1–5 ms; upstream API 50–2000 ms; model think+decode 500 ms–tens of s. Optimize the **upstream** and **approval UI**, not the RPC codec. Stateless `2026-07-28` removes sticky-session p99 spikes from session-store failover (Cloudflare/AWS quotes on the spec blog).

| Percentile | Working target | Mitigation | Label |
| --- | --- | --- | --- |
| p50 | TLS+JSON-RPC **≪** upstream; LLM Fast p50 > **80 tok/s** (Sol) is **not** MCP RTT | `allowed_tools`; skip HITL only after trust (`require_approval: never`); stable catalog so `tools/list` is cached | Fast tok/s = published LLM; MCP RTT = **[inferred]** |
| p95 | Upstream API + approval modal; SSE proxy buffering looks like multi-second p95 | `X-Accel-Buffering: no`; SSE `:` keep-alives; honor `pollIntervalMs`; don’t buffer listen streams at nginx/CDN | Buffering = spec MUST/SHOULD; numeric p95 **[inferred]** |
| p99 | Replica death + no `Last-Event-ID`; Tasks/handles replay; session-sticky old SDKs against a stateless farm | Round-robin OK on 2026-07-28; persist `taskId` / handles; migrate off `Mcp-Session-Id`; circuit-break the dead server, keep others | Stream resume **not supported** (spec); p99 **[inferred]** |

Approval vs no-approval is qualitative (OpenAI: skip approvals for “reduced latency” after trust). Honeycomb anecdote: ~**20%** of monthly interactive queries via MCP after the spec change — **not** a latency SLO.

### 3.3 Throughput and back-pressure

OpenAI **MCP-specific RPM** (Responses MCP tool): Tier 1 **200 RPM**; Tiers 2–3 **1000 RPM**; Tiers 4–5 **2000 RPM**. ⚠️ No matching Anthropic MCP-connector RPM table in the connector doc. This cap is independent of the model TPM table — hosted MCP can 429 while the LLM still has token budget.

**Back-pressure design:**

1. Gateway admits only if per-server breaker is closed/half-open **and** the MCP RPM bucket has room **and** (Tasks) pollers honor `pollIntervalMs`.
2. Task poll storms: ignoring `pollIntervalMs` is a named production failure; prefer `notifications/tasks`.
3. Over-admission of `tools/list` on hosted connectors burns first-byte latency and descriptor tokens; `ttlMs` + hash-pin.
4. Cursor: failed MCP call is isolated; other servers continue — do **not** couple breakers across server ids.
5. Dynamic connect: progressive discovery connects Salesforce only when `enable_server` fires; disconnect at task end to free context.

stdio fan-out = 1 client (subprocess). Streamable HTTP fan-out = many; LB round-robin is correct on `2026-07-28`. Long jobs: blocking POST/SSE dies on proxy timeout → **Tasks extension**.

### 3.4 Non-functional requirements and explicit trade-offs

| NFR | Working target | Tension |
| --- | --- | --- |
| Availability | MCP spec has none. OpenAI Fast **99.9%** is the **model** API. Host: isolate per server (Cursor). Gateway: multi-router (Microsoft) + Envoy steer around 429 backends | Extra hop vs blast radius of N direct sockets |
| RPO | Handles/Tasks/`requestState` nonce store: **0** for in-flight elicitation and irreversible tools. `tools/list` cache: **ttlMs** (example 5 min), stale-on-error allowed | Treating catalog cache as RPO=0 causes `public` leaks and rug-pull blindness |
| RTO | Interactive: fail over < 1 s to secondary server or skip the dead one. Long jobs: resume `taskId` on any replica; do not re-POST the original SSE | Fast skip vs identical in-flight tool results (SSE lost on death) |
| Consistency | Tool writes: **exactly-once via idempotency + handle authz re-check**. Catalog: `list_changed` beats TTL. Model text: at-least-once retry may change tokens | Cannot have bit-identical retry on temperature>0 |
| Compliance | Audience-bound tokens; no passthrough; ZDR/data-residency **stops at the MCP hop** (third party has its own retention); Anthropic remote = data leaves the enterprise to Anthropic **then** the server; OpenAI `store=true` retains 30 days unless ZDR | claude.ai cannot reach private-VPC MCP; Desktop stdio or OpenAI tunnel |
| Cost vs latency | Descriptor dump **$0.140/turn** uncached vs **$0.014** cached vs progressive discovery extra RTTs; HITL skip lowers latency, raises blast radius; Fast mode **2× $** | Paying Fast / skipping HITL to hide MCP RTT you did not measure |
| Consistency vs availability | Sticky old-SDK sessions (elicitation works until the replica dies) vs stateless round-robin (available, `requestState` must be AEAD) | Cloudflare dual-speak `/mcp` during migration |

---

## 4. Distributed Resilience & Security

### 4.1 Durable execution (Temporal / Kafka)

MCP `2026-07-28` already tells you what must be durable: **handles in arguments**, **Tasks `taskId`**, **MRTR nonce/`requestState`**, **subscription re-listen**. There is **no** shared session store. Map that onto the enterprise runtimes used for agent loops:

| MCP concept | Temporal | Why |
| --- | --- | --- |
| Host agent loop / HITL approval | **Workflow** (deterministic) | Replay from Event History; idle elicitation = **zero compute** |
| `tools/call`, `resources/read`, OAuth token exchange, upstream HTTP | **Activity** | Recorded once; replay must **not** re-call the LLM or re-fire a write |
| Tasks extension | Child workflow **or** Activity with heartbeat; store `taskId` in workflow state | Poll any replica if task store is shared; cooperative `tasks/cancel` |
| MRTR `requestState` single-use | Workflow-local nonce **or** compare-and-set in the nonce store | The only required shared state for one-time redemption |
| Catalog `ttlMs` + hash | Side-effect-free Activity; pin hash in workflow input | `list_changed` = new decision, not silent mutation |

> ⚠️ Gap: the MCP research file does not publish Temporal worker-versioning schemes or measured replay cost for multi-MB tool payloads. Treat Temporal here as the durability mapping of spec handles/Tasks/MRTR, not as an MCP feature.

**Kafka (log = chain of custody).** Topics per tenant-shard: `mcp.call.intent`, `mcp.call.result`, `mcp.catalog.diff`, `mcp.dlq`. Produce the **intent** (`Mcp-Name` + idempotency key + hashed args + `token_jti`) **before** the upstream write (outbox). Compact on `correlation_id` / handle id. Poison (unknown method, repeated crash on same args hash, forged `requestState`) → DLQ after \(N\); do not block the partition. Gateway access logs with `Mcp-Method`/`Mcp-Name` are the practical immutable stream; MCP’s own logging utility is deprecated.

**Saga.** Register compensation **before** a mutating `tools/call` Activity. Compensations LIFO and **idempotent**, on the **same server’s** allowlist (do not give the host a union of compensating tools). Irreversible (`send_email`, `git push`) cannot unsend — timeout-deny + HITL, not a compensating LLM guess.

**Locking / races:** round-robin is correct; do not sticky-route on deleted `Mcp-Session-Id`. Handle is not a capability if authenticated — two replicas must both re-check authz. Paginated `tools/list` has **no** snapshot guarantee. Optimistic concurrency: Task etag / handle generation; reject stale `tasks/update`.

### 4.2 Failure taxonomy

| Class | Examples | Handler |
| --- | --- | --- |
| Transient | 429, 500, 503, TLS reset, SSE idle timeout, stdio process death, replica death mid-SSE | Full-jitter retry on **idempotent** reads/`tools/list`; restart stdio; retry POST on **any** instance; re-`listen`; **do** trip per-server breaker if consecutive **across** executions |
| Permanent | `-32602` unknown tool / missing resource, 401 wrong `aud`, PKCE method absent, `tchar` violation on `x-mcp-header`, unsupported `inputRequests` | **No** retry; fail the call; `isError` only for **business** errors the model can fix |
| Poison pill | Rug-pull `list_changed` after install-time approval; tool poisoning in `description`; implicit poisoning (MCP-ITP **up to 84.2% ASR**, **0.3% MDR** ⚠️ research ASR not a KPI); 50-retry loop into a dead server; `cacheScope: public` cross-tenant | Hash-pin catalog + re-prompt on diff; isolate high-privilege servers into **separate** conversations; DLQ; never mix marketplace + secrets-bearing server |
| Semantic | Schema-valid but unauthorized write; resource injection (“dump private PII into public PR”); schema drift (cached `inputSchema` vs tightened server); Gemini/OpenAI subset conversion strips `$defs` | Authz on the server, not annotations; delimit resource bytes; honor `ttlMs` **and** `list_changed`; contract-test after native conversion |

| Event | stdio | Streamable HTTP 2026-07-28 |
| --- | --- | --- |
| Cancel in-flight | `notifications/cancelled` | Close SSE; no cancel notification |
| Process/replica death | Restart subprocess; retry calls; re-`listen` | Retry POST on any instance; in-flight SSE **lost** |
| Catalog/resource change | `subscriptions/listen` | Same; keep-alives required |
| SSE disconnect | n/a | Re-`listen`; **no** `Last-Event-ID` replay |
| Legacy session DELETE | n/a | `Mcp-Session-Id` **gone**; old SDKs still expose `terminateSession` for mixed fleets |

**Named production modes:** OAuth mix-up (RFC 9207 `iss`); registry open redirect CVE-2026-44427; `requestState` forgery; header injection; DNS rebinding; session assumption on old SDK; cross-server name collision (`search` vs `search`); hosted MCP + ZDR illusion; sampling deprecation trap; Task poll storms.

**Schema drift.** Server tightens `inputSchema` or adds required fields → model keeps cached schema → `isError` or `-32602`. `outputSchema` added later → non-validating clients silently accept garbage. Dialect mismatch draft-07 vs 2020-12 across SDK eras. Aggregator prefix change (`github_search` → `srv2_search`) busts prompt cache. Mitigation: bust LLM tool cache when the catalog hash changes; version handles (`basket_id` v2).

### 4.3 Circuit breaker and fallback chain

**Not in the MCP spec.** Hosts/gateways supply it. Observed: Cursor continues other servers on crash/timeout; Envoy 1.0 steers around rate-limited MCP backends; Microsoft gateway runs multiple router instances. **[inferred]** client circuit: after N consecutive `isError`/transport failures, trip **that server** for T seconds, keep others; do not poison the model with a 50-retry loop.

```
CLOSED ──(consecutive failures ≥ N on this server_id)──▶ OPEN ──(cooldown)──▶ HALF_OPEN
  ▲                                                     │ fail fast                    │
  │                                                     │ skip server / fallback       ├── probe OK ──▶ CLOSED
  └─────────────────────────────────────────────────────┴──────────────────────────────┘ probe fail ──▶ OPEN
```

| Error | Retry Activity? | Open breaker? |
| --- | --- | --- |
| 429, 500, 503, timeout, stdio death | Yes, full jitter, honor Retry-After | If consecutive **across** executions |
| `-32602`, 401 wrong aud, 400 schema | **No** | No (logic / policy) |
| `isError: true` business | No (model self-corrects) | Only if rate of business errors is a **transport** outage you measured |

**Fallback chain (research order):**

1. **Primary MCP server** behind a closed breaker (stdio **or** Streamable HTTP).
2. **Secondary replica / second server** with the same allowlisted tool (gateway steer; Cursor isolation). Serve **stale** `tools/list` on refetch error if `ttlMs` allows.
3. **Degrade:** drop that server from the model’s tool set this turn (progressive disconnect); keep other servers. Code-mode summaries instead of dumping 800-token results.
4. **Deterministic escalate** — structured `isError` / HITL so parsers do not crash. Never fall back to **token passthrough** or “host calls upstream with the user’s MCP Bearer.”

Hedging: duplicate a straggler **read** `resources/read`; cancel loser. Do not hedge `push` / `charge`.

### 4.4 Zero-Trust MCP (thorough)

MCP’s own spec says the protocol **cannot** enforce consent; implementors **SHOULD**. Zero-Trust for MCP means: **never trust the tool catalog, the resource body, the annotation, the token audience, or the peer’s `cacheScope`.** Map to NIST SP 800-207: authenticate every request, authorize per-action, assume breach, log everything. **[inferred]** MCP is a new PEP/PDP pair in front of existing APIs.

**Trust principles in the spec:** (1) user consent and control — explicit, revocable, UI-visible; (2) data privacy — hosts must not ship user data to servers or onward without consent; (3) tool safety — tools = arbitrary execution; descriptions/annotations untrusted unless the server is trusted; confirm before invoke.

Claude Code: servers that fetch external content expose **prompt-injection**. OpenAI: malicious remote MCP can **exfiltrate anything in model context**; defaults to per-call approval; report `security@openai.com`.

**Zero-Trust token rules (non-negotiable):**

1. **Audience binding (RFC 8707 / RFC 9068)** — token `aud` is **this** MCP server URI. Reject anything else.
2. **No token passthrough** — bypasses MCP-layer rate limits, schema validation, and audit (downstream logs show the wrong actor). A token stolen for Service A becomes a confused-deputy key for Service B if B doesn’t check `aud`. Future controls (step-up, tool-level RBAC) are unimplementable if the server is a dumb pipe. Upstream = a **new** token from the upstream AS.
3. **Short-lived access tokens; rotate refresh tokens for public clients** (OAuth 2.1 §4.3.1 / §7.1). Store in OS keychain / confidential store, not `mcp.json` plaintext. Cursor interpolation: `${env:NAME}`.
4. **Per-request `_meta`** — no ambient session identity. Authorization presented **on that request** may change `tools/list`.
5. **Least-privilege catalogs** — filtered `tools/list`; `allowed_tools`; progressive discovery. Empty Cursor tool list = all tools on that server — **do not copy that default** into an enterprise gateway; fail closed.
6. **Network egress** — Cursor/VS Code sandbox; SSRF allowlist for OAuth URLs. Malicious `resource_metadata` → `http://169.254.169.254/`. Clients **SHOULD** HTTPS-only (loopback exception), block RFC 1918 / link-local / ULA, not follow redirects to internals, use egress proxies. Do **not** hand-roll IP parsers (octal/hex/v4-mapped). CIMD makes the **AS** an SSRF client too (RFC 9728 §7.7).
7. **DNS rebinding** — `Origin` check + localhost bind.
8. **Supply-chain pin** — hash tool descriptors; registry namespace proof; prefer first-party hosts (`mcp.stripe.com` not a proxy). Assume poisoned catalog: show full descriptions in HITL; pin versions; `list_changed` = re-review.
9. **Revocation** — IdP session kill (EMA) or refresh rotation; handle TTL.
10. **Telemetry** — OTel `traceparent` in `_meta`; gateway access logs. Annotations are **not** a control signal.

**EMA (`io.modelcontextprotocol/enterprise-managed-authorization`, SEP-990):** employee SSO to the **host**; IdP issues **ID-JAG**; MCP AS exchanges ID-JAG for an MCP access token. Policy (group, CA, device) lives in Okta/Entra, not per-server consent screens. Revoke at the IdP once. Servers validate ID-JAG (JWKS, `iss`/`aud`/`exp`); subject claim is the stable user id. Machine-to-machine: OAuth client-credentials extension (SEP-1046).

**Zero-Trust MCP control-plane checklist**

| Control | Where |
| --- | --- |
| Strong identity (workforce SSO / workload identity) | EMA or CIMD+PKCE; no long-lived static Bearer in git |
| Per-request authz (scope + tool name + resource URI) | Gateway on `Mcp-Name` + server-side check; never trust annotations |
| Audience-bound tokens | RFC 8707 `resource`; reject wrong `aud` |
| No token passthrough | New upstream credential every hop |
| Least-privilege catalogs | Filtered `tools/list`; `allowed_tools`; progressive discovery |
| Network egress policy | Cursor/VS Code sandbox; SSRF allowlist for OAuth URLs |
| Supply-chain pin | Hash descriptors; registry namespace; first-party hosts |
| Assume poisoned catalog | Full HITL text; pin versions; `list_changed` = re-review |
| Telemetry | OTel in `_meta`; gateway access logs |
| Revocation | IdP kill (EMA) or refresh rotation; handle TTL |

### 4.5 Tool RBAC, PII, immutable logs

**RBAC.** Protocol primitive = OAuth **scopes** + per-request filtered `tools/list` / `resources/list`. Hosts add a second gate: Cursor **tool allowlists** inside an MCP allowlist; Claude API allowlist/denylist on `mcp_toolset`; OpenAI `allowed_tools` + `require_approval`; Foundry Toolbox: Entra + Azure Policy. **[inferred]** Fine-grained “this agent may `issues.write` on repo X only” is **not** in MCP — encode it in the server’s token exchange / ABAC, or a gateway.

**Sandbox.** Spec: stdio is full local code exec. VS Code: `sandboxEnabled` + filesystem/network allowlists (macOS/Linux; **not Windows**). Cursor enterprise: per-server network Allow all / Allowlist / Deny all / No sandbox; User MCP denylist. Code-mode: Deno/`isolated-vm`/Wasmtime with **deny-all net**; broker only. SEP-1024: client security requirements for **local** server install.

**PII pipeline:** detect → redact **before** any `tools/call` / `resources/read` leaves the host **and** before form elicitation is echoed to the model → audit placeholders (hash, never raw). Treat `resources/read`, tool args, and form elicitation as PII pipes. Ban secrets from form mode and from `x-mcp-header`. OpenAI: URLs/images from tool output are SSRF/exfil vectors; ZDR **stops at the MCP hop**. Every extra hosted hop (Anthropic IPs → server) is a **copy**.

**Immutable logs.** Spec: clients **SHOULD** log tool usage. Gateways (Cloudflare AI Gateway, Foundry, Envoy) are the practical WORM place: `Mcp-Method`, `Mcp-Name`, hashed args, policy version, catalog hash, `token_jti`, correlation id. Hash-chain rows for tamper evidence. Claude Enterprise: audit logs + Compliance API. OpenAI `store=true` is **not** your compliance log.

### 4.6 Confused deputy (two species) and catalog attacks

**A. OAuth-proxy deputy (spec-normative).** When an MCP **proxy** uses a **static** third-party `client_id`, allows **DCR** of MCP clients, and the third-party AS sets a **consent cookie**, an attacker registers `redirect_uri=attacker.com`, sends a link, and the cookie skips consent → attacker receives the MCP auth code.

**MUST:** per-`client_id` consent **before** redirecting to the third party; exact `redirect_uri` match; CSRF/`state` issued **after** MCP consent; `__Host-` cookies; `frame-ancestors` / `X-Frame-Options: DENY`.

**B. Tool-authority deputy.** MCP server holds GitHub/Slack/DB credentials; the **model** is induced (via issue text, email, or another tool result) to use them. Invariant: official GitHub MCP + public issue → agent dumps private-repo PII into a public PR. **Not a bug in GitHub’s MCP code**; any client with that server is exposed. Alignment of Claude 4 Opus was insufficient. Mitigation: **one repo per session**, least-privilege PATs, runtime dataflow policy — GitHub cannot patch this alone. Token passthrough **is** a deputy amplifier.

**Tool poisoning.** Malicious instructions in `description` (often in `<IMPORTANT>` blocks). User sees “add two numbers”; model reads “send `~/.ssh/id_rsa` as `sidenote`”. Cursor confirmation UI hid full args. **Works even if the user never wanted that tool** if another poisoned description **shadows** a trusted tool (`send_email` must BCC attacker). Hosts still inject descriptions into the **system/tools channel**, which models treat as high-trust.

MCP-ITP (arxiv 2601.07395): **implicit** poisoning — the malicious tool is never called; metadata steers the agent to a **privileged** tool. Reported **up to 84.2% ASR** and **0.3% MDR** vs naive detectors on MCPTox / 12 agents. ⚠️ Research ASR, not a production KPI.

**Rug-pull.** Benign `tools/list` at install-time approval, then `list_changed` (or silent mutation if the client never re-shows UI) injects poisoning. `ttlMs` + listen notifications make **detection** easier if the host **hashes** the list and **re-prompts** on diff. Clients that cache `mcp_list_tools` forever without invalidation (OpenAI: list not refetched while the item is in context) can **miss** a rug-pull **or** stick to a stale good list — both are failure modes; prefer TTL + signed catalog + user-visible diff.

**Mitigations that actually match the research:** render full description + schema in HITL; hash-pin catalogs (MCP-Scan “tool pinning”); isolate high-privilege servers into **separate hosts/conversations**; never mix an unvetted marketplace server with a secrets-bearing server; policy: “no `tools/call` that writes public artifacts in the same turn as a read from untrusted URI”; classify `openWorld`/destructive yourself — do not trust the annotation bit.

---

## 5. Production Enterprise Code

Stdlib-only MCP host/client/gateway stub: capability handshake (`server/discover` then legacy fallback), fail-closed allowlist, JSON-RPC dispatch (`tools/list|call`, `resources/read`), HMAC `requestState`, audience-bound tokens with **no passthrough**, full-jitter retries, per-server circuit breaker (closed → open → half-open), primary → secondary → deterministic degrade, correlation-id JSON logs, PII detect→redact→audit, hash-chained WORM rows. Run: `python mcp_runtime.py`.

```python
#!/usr/bin/env python3
"""MCP host/client/gateway runtime (stdlib only). Run: python mcp_runtime.py"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import random
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

PROTOCOL = "2026-07-28"
POLICY_VERSION = "mcp-2026-08-21"
BREAKER_FAILURES = 3
BREAKER_RECOVERY_S = 0.05
HMAC_KEY = b"mcp-request-state-demo-key"
TCHAR = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")

class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "msg": record.getMessage(),
            "logger": record.name,
            "correlation_id": getattr(record, "correlation_id", None),
            "tenant": getattr(record, "tenant", None),
            "server_id": getattr(record, "server_id", None),
            "mcp_method": getattr(record, "mcp_method", None),
            "mcp_name": getattr(record, "mcp_name", None),
            "breaker": getattr(record, "breaker", None),
            "degraded": getattr(record, "degraded", None),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class CorrelationAdapter(logging.LoggerAdapter):
    def process(self, msg: str, kwargs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        extra = dict(self.extra)
        extra.update(kwargs.pop("extra", {}))
        kwargs["extra"] = extra
        return msg, kwargs


def build_logger(correlation_id: str, tenant: str) -> CorrelationAdapter:
    base = logging.getLogger("mcp.runtime")
    if not base.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonLogFormatter())
        base.addHandler(handler)
        base.setLevel(logging.INFO)
        base.propagate = False
    return CorrelationAdapter(base, {"correlation_id": correlation_id, "tenant": tenant})


_PII_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("email", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)),
)


def redact_pii(text: str) -> tuple[str, list[dict[str, str]]]:
    audit: list[dict[str, str]] = []
    out = text
    for label, pat in _PII_PATTERNS:
        def _sub(m: re.Match[str], _label: str = label) -> str:
            digest = hashlib.sha256(m.group(0).encode()).hexdigest()[:12]
            token = f"<{_label}:{digest}>"
            audit.append({"type": _label, "placeholder": token})
            return token
        out = pat.sub(_sub, out)
    return out, audit


class TransientError(Exception):
    pass


class PermanentError(Exception):
    pass


class CircuitOpenError(Exception):
    pass


class AuthzError(PermanentError):
    pass


class BreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: int = BREAKER_FAILURES,
        recovery_seconds: float = BREAKER_RECOVERY_S,
        half_open_max: int = 1,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self.half_open_max = half_open_max
        self._state = BreakerState.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._half_open_inflight = 0
        self._lock = threading.Lock()

    @property
    def state(self) -> BreakerState:
        with self._lock:
            self._maybe_half_open()
            return self._state

    def _maybe_half_open(self) -> None:
        if self._state is BreakerState.OPEN and (
            time.monotonic() - self._opened_at
        ) >= self.recovery_seconds:
            self._state = BreakerState.HALF_OPEN
            self._half_open_inflight = 0

    def allow(self) -> None:
        with self._lock:
            self._maybe_half_open()
            if self._state is BreakerState.OPEN:
                raise CircuitOpenError("circuit open")
            if self._state is BreakerState.HALF_OPEN:
                if self._half_open_inflight >= self.half_open_max:
                    raise CircuitOpenError("half-open probe in flight")
                self._half_open_inflight += 1

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._half_open_inflight = 0
            self._state = BreakerState.CLOSED

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._state is BreakerState.HALF_OPEN or self._failures >= self.failure_threshold:
                self._state = BreakerState.OPEN
                self._opened_at = time.monotonic()
                self._half_open_inflight = 0


def retry_call(
    fn: Callable[[], Any],
    *,
    attempts: int = 4,
    base_seconds: float = 0.05,
    max_seconds: float = 0.4,
) -> Any:
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except TransientError as exc:
            last = exc
            if i == attempts - 1:
                break
            cap = min(max_seconds, base_seconds * (2**i))
            time.sleep(random.random() * cap)
    assert last is not None
    raise last


@dataclass(frozen=True)
class AccessToken:
    aud: str
    sub: str
    scopes: frozenset[str]
    exp_mono: float
    jti: str


def check_token(token: AccessToken, resource: str, scope: str) -> None:
    if time.monotonic() > token.exp_mono:
        raise AuthzError("expired")
    if token.aud != resource:
        raise AuthzError(f"aud mismatch: {token.aud} != {resource}")
    if scope not in token.scopes:
        raise AuthzError(f"missing scope {scope}")


def mint_upstream_token(server_id: str, audience: str) -> str:
    # New credential every hop. Never return the inbound MCP Bearer.
    return f"ups.{server_id}.{audience}.{uuid.uuid4().hex[:8]}"


def seal_request_state(principal: str, method: str, ttl_s: float = 60.0) -> str:
    payload = {
        "principal": principal,
        "method": method,
        "exp": time.time() + ttl_s,
        "nonce": uuid.uuid4().hex,
    }
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    sig = hmac.new(HMAC_KEY, body, hashlib.sha256).hexdigest()
    return json.dumps({"body": payload, "sig": sig}, separators=(",", ":"))


def open_request_state(blob: str, principal: str, method: str, seen: set[str]) -> dict[str, Any]:
    obj = json.loads(blob)
    body = json.dumps(obj["body"], separators=(",", ":"), sort_keys=True).encode()
    expect = hmac.new(HMAC_KEY, body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expect, obj["sig"]):
        raise AuthzError("requestState forged")
    inner = obj["body"]
    if inner["principal"] != principal or inner["method"] != method:
        raise AuthzError("requestState bind mismatch")
    if inner["exp"] < time.time():
        raise AuthzError("requestState expired")
    if inner["nonce"] in seen:
        raise AuthzError("requestState replay")
    seen.add(inner["nonce"])
    return inner


@dataclass
class WormLog:
    rows: list[dict[str, Any]] = field(default_factory=list)
    _prev: str = "genesis"

    def append(self, row: dict[str, Any]) -> None:
        material = json.dumps({"prev": self._prev, **row}, sort_keys=True, default=str)
        digest = hashlib.sha256(material.encode()).hexdigest()
        stored = {**row, "prev": self._prev, "digest": digest}
        self.rows.append(stored)
        self._prev = digest


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    x_mcp_header: dict[str, str] | None = None


class McpServer:
    """In-process Streamable-HTTP stand-in: JSON-RPC dispatch, no sessions."""

    def __init__(self, server_id: str, resource: str, tools: list[ToolSpec], resources: dict[str, str]) -> None:
        self.server_id = server_id
        self.resource = resource
        self.tools = {t.name: t for t in tools}
        self.resources = resources
        self.fail_transport = False
        self.seen_nonces: set[str] = set()
        self.catalog_hash = self._hash_catalog()

    def _hash_catalog(self) -> str:
        blob = json.dumps(
            [(n, self.tools[n].description) for n in sorted(self.tools)],
            separators=(",", ":"),
        )
        return hashlib.sha256(blob.encode()).hexdigest()[:16]

    def discover(self) -> dict[str, Any]:
        return {
            "supportedVersions": [PROTOCOL],
            "capabilities": {
                "tools": {"listChanged": True},
                "resources": {"subscribe": True, "listChanged": True},
            },
            "ttlMs": 300000,
            "cacheScope": "private",
            "serverInfo": {"name": self.server_id, "version": "1"},
        }

    def dispatch(self, req: dict[str, Any], token: AccessToken) -> dict[str, Any]:
        if self.fail_transport:
            raise TransientError(f"{self.server_id} transport down")
        method = req.get("method")
        req_id = req.get("id")
        params = req.get("params") or {}
        try:
            check_token(token, self.resource, "mcp")
            if method == "server/discover":
                result = self.discover()
            elif method == "tools/list":
                result = {
                    "tools": [
                        {
                            "name": t.name,
                            "description": t.description,
                            "inputSchema": t.input_schema,
                        }
                        for t in self.tools.values()
                    ]
                }
            elif method == "tools/call":
                result = self._call(params, token)
            elif method == "resources/read":
                result = self._read(params)
            else:
                return _rpc_error(req_id, -32601, f"method not found: {method}")
            return {"jsonrpc": "2.0", "id": req_id, "result": result}
        except AuthzError as exc:
            return _rpc_error(req_id, -32602, str(exc))
        except KeyError as exc:
            return _rpc_error(req_id, -32602, str(exc))
        except PermanentError as exc:
            return _rpc_error(req_id, -32602, str(exc))

    def _call(self, params: dict[str, Any], token: AccessToken) -> dict[str, Any]:
        name = params["name"]
        args = params.get("arguments") or {}
        if name not in self.tools:
            raise PermanentError(f"unknown tool {name}")
        spec = self.tools[name]
        if spec.x_mcp_header:
            for key, value in spec.x_mcp_header.items():
                if not TCHAR.match(key) or not TCHAR.match(str(value)):
                    raise PermanentError("x-mcp-header tchar violation")
        if name == "lookup_ticket":
            text = self.resources.get(str(args.get("uri", "")), "")
            if not text:
                raise PermanentError("resource not found")
            return {
                "resultType": "complete",
                "isError": False,
                "content": [{"type": "text", "text": text}],
                "upstream_token_prefix": mint_upstream_token(self.server_id, "tickets")[:24],
            }
        if name == "create_note":
            if "approved" not in args:
                state = seal_request_state(token.sub, "tools/call")
                return {
                    "resultType": "input_required",
                    "requestState": state,
                    "inputRequests": {"form": {"properties": {"approved": {"type": "boolean"}}}},
                }
            open_request_state(params["requestState"], token.sub, "tools/call", self.seen_nonces)
            if args.get("approved") is not True:
                return {"resultType": "complete", "isError": True, "content": [{"type": "text", "text": "declined"}]}
            return {
                "resultType": "complete",
                "isError": False,
                "content": [{"type": "text", "text": "note-created"}],
                "upstream_token_prefix": mint_upstream_token(self.server_id, "notes")[:24],
            }
        raise PermanentError(f"unhandled tool {name}")

    def _read(self, params: dict[str, Any]) -> dict[str, Any]:
        uri = params.get("uri")
        if uri not in self.resources:
            raise PermanentError("resource not found")
        return {"contents": [{"uri": uri, "mimeType": "text/plain", "text": self.resources[uri]}]}


def _rpc_error(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


class McpClient:
    """One client ↔ one server. Host concatenates N of these."""

    def __init__(self, server: McpServer, allowlist: frozenset[str], log: CorrelationAdapter, worm: WormLog) -> None:
        self.server = server
        self.allowlist = allowlist
        self.log = log
        self.worm = worm
        self.breaker = CircuitBreaker()
        self.cached_discover: dict[str, Any] | None = None
        self.handshake_ok = False

    def handshake(self, token: AccessToken) -> dict[str, Any]:
        def _probe() -> dict[str, Any]:
            return self._rpc("server/discover", {}, token)

        try:
            result = retry_call(_probe, attempts=3)
        except (TransientError, CircuitOpenError):
            raise PermanentError("discover failed; would try legacy initialize next")
        versions = result.get("supportedVersions") or []
        if PROTOCOL not in versions:
            raise PermanentError(f"unsupported versions {versions}")
        if result.get("cacheScope") == "public":
            raise PermanentError("refusing public catalog cacheScope for token-filtered tools")
        self.cached_discover = result
        self.handshake_ok = True
        self.log.info("handshake ok", extra={"server_id": self.server.server_id, "mcp_method": "server/discover"})
        return result

    def _rpc(self, method: str, params: dict[str, Any], token: AccessToken) -> dict[str, Any]:
        self.breaker.allow()
        req = {
            "jsonrpc": "2.0",
            "id": uuid.uuid4().hex,
            "method": method,
            "params": params,
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": PROTOCOL,
                "clientInfo": {"name": "enterprise-host", "version": "1"},
                "clientCapabilities": {"elicitation": {"form": True, "url": True}},
            },
        }
        try:
            resp = self.server.dispatch(req, token)
        except TransientError:
            self.breaker.record_failure()
            raise
        if "error" in resp:
            self.breaker.record_success()
            raise PermanentError(f"{resp['error']['code']} {resp['error']['message']}")
        self.breaker.record_success()
        return resp["result"]

    def tools_list(self, token: AccessToken) -> list[dict[str, Any]]:
        raw = self._rpc("tools/list", {}, token)
        kept = []
        for tool in raw["tools"]:
            if tool["name"] not in self.allowlist:
                continue
            if tool.get("x-mcp-header"):
                continue
            kept.append(tool)
        kept.sort(key=lambda t: t["name"])
        self.worm.append(
            {
                "event": "tools/list",
                "server_id": self.server.server_id,
                "catalog_hash": self.server.catalog_hash,
                "names": [t["name"] for t in kept],
                "policy": POLICY_VERSION,
            }
        )
        return kept

    def call(self, name: str, arguments: dict[str, Any], token: AccessToken, request_state: str | None = None) -> dict[str, Any]:
        if name not in self.allowlist:
            raise PermanentError(f"allowlist deny {name}")
        redacted_args, pii = {}, []
        for k, v in arguments.items():
            if isinstance(v, str):
                rv, hits = redact_pii(v)
                redacted_args[k] = rv
                pii.extend(hits)
            else:
                redacted_args[k] = v
        params: dict[str, Any] = {"name": name, "arguments": redacted_args}
        if request_state is not None:
            params["requestState"] = request_state
        result = self._rpc("tools/call", params, token)
        self.worm.append(
            {
                "event": "tools/call",
                "server_id": self.server.server_id,
                "mcp_name": name,
                "args_hash": hashlib.sha256(json.dumps(redacted_args, sort_keys=True).encode()).hexdigest()[:16],
                "pii": pii,
                "token_jti": token.jti,
                "resultType": result.get("resultType"),
                "policy": POLICY_VERSION,
            }
        )
        if "content" in result:
            for block in result["content"]:
                if block.get("type") == "text":
                    block["text"], hits = redact_pii(block["text"])
                    if hits:
                        self.worm.append({"event": "pii_redact", "hits": hits, "policy": POLICY_VERSION})
        return result


class HostGateway:
    """N independent clients; skip open breakers; never share inbound tokens upstream."""

    def __init__(self, clients: dict[str, McpClient], log: CorrelationAdapter) -> None:
        self.clients = clients
        self.log = log

    def call_with_fallback(
        self,
        order: list[str],
        name: str,
        arguments: dict[str, Any],
        token: AccessToken,
        request_state: str | None = None,
    ) -> dict[str, Any]:
        errors: list[str] = []
        for server_id in order:
            client = self.clients[server_id]
            try:
                result = retry_call(
                    lambda c=client: c.call(name, arguments, token, request_state),
                    attempts=3,
                )
                self.log.info(
                    "call ok",
                    extra={
                        "server_id": server_id,
                        "mcp_method": "tools/call",
                        "mcp_name": name,
                        "breaker": client.breaker.state.value,
                    },
                )
                return result
            except CircuitOpenError as exc:
                errors.append(f"{server_id}:open:{exc}")
                self.log.info(
                    "skip open circuit",
                    extra={"server_id": server_id, "breaker": "open", "degraded": True, "mcp_name": name},
                )
            except TransientError as exc:
                errors.append(f"{server_id}:transient:{exc}")
            except PermanentError as exc:
                errors.append(f"{server_id}:permanent:{exc}")
                break
        return {
            "resultType": "complete",
            "isError": True,
            "degraded": True,
            "content": [{"type": "text", "text": "escalate_to_human"}],
            "errors": errors,
        }


def _demo() -> None:
    cid = str(uuid.uuid4())
    log = build_logger(cid, "acme")
    worm = WormLog()
    ticket_body = "Reporter alice@acme.test SSN 123-45-6789: restart laptop"
    primary = McpServer(
        "tickets-a",
        "https://mcp.acme.example/tickets",
        [
            ToolSpec(
                "lookup_ticket",
                "Look up a ticket by URI",
                {"type": "object", "properties": {"uri": {"type": "string"}}, "required": ["uri"], "additionalProperties": False},
            ),
            ToolSpec(
                "create_note",
                "Create a note after HITL",
                {"type": "object", "properties": {"approved": {"type": "boolean"}}, "additionalProperties": False},
            ),
            ToolSpec("wipe_disk", "not allowlisted", {"type": "object", "additionalProperties": False}),
        ],
        {"ticket://1001": ticket_body},
    )
    secondary = McpServer(
        "tickets-b",
        "https://mcp.acme.example/tickets",
        [
            ToolSpec(
                "lookup_ticket",
                "Look up a ticket by URI",
                {"type": "object", "properties": {"uri": {"type": "string"}}, "required": ["uri"], "additionalProperties": False},
            ),
        ],
        {"ticket://1001": ticket_body},
    )
    allow = frozenset({"lookup_ticket", "create_note"})
    client_a = McpClient(primary, allow, log, worm)
    client_b = McpClient(secondary, frozenset({"lookup_ticket"}), log, worm)
    gateway = HostGateway({"tickets-a": client_a, "tickets-b": client_b}, log)
    token = AccessToken(
        aud="https://mcp.acme.example/tickets",
        sub="user-1",
        scopes=frozenset({"mcp"}),
        exp_mono=time.monotonic() + 3600,
        jti=uuid.uuid4().hex,
    )
    wrong = AccessToken(
        aud="https://mcp.other.example",
        sub="user-1",
        scopes=frozenset({"mcp"}),
        exp_mono=time.monotonic() + 3600,
        jti="x",
    )
    disc = client_a.handshake(token)
    assert PROTOCOL in disc["supportedVersions"]
    listed = client_a.tools_list(token)
    assert [t["name"] for t in listed] == ["create_note", "lookup_ticket"]
    try:
        client_a.call("wipe_disk", {}, token)
        raise SystemExit("allowlist must deny")
    except PermanentError:
        pass
    denied = primary.dispatch(
        {"jsonrpc": "2.0", "id": "1", "method": "tools/call", "params": {"name": "lookup_ticket", "arguments": {"uri": "ticket://1001"}}},
        wrong,
    )
    assert denied["error"]["code"] == -32602
    looked = gateway.call_with_fallback(["tickets-a", "tickets-b"], "lookup_ticket", {"uri": "ticket://1001"}, token)
    assert looked["isError"] is False
    assert "<email:" in looked["content"][0]["text"]
    assert "<ssn:" in looked["content"][0]["text"]
    assert looked["upstream_token_prefix"].startswith("ups.")
    mrtr = client_a.call("create_note", {}, token)
    assert mrtr["resultType"] == "input_required"
    done = client_a.call("create_note", {"approved": True}, token, mrtr["requestState"])
    assert done["isError"] is False
    try:
        client_a.call("create_note", {"approved": True}, token, mrtr["requestState"])
        raise SystemExit("replay must fail")
    except PermanentError:
        pass
    missing = primary.dispatch(
        {"jsonrpc": "2.0", "id": "2", "method": "resources/read", "params": {"uri": "ticket://nope"}},
        token,
    )
    assert missing["error"]["code"] == -32602
    primary.fail_transport = True
    for _ in range(BREAKER_FAILURES):
        try:
            retry_call(lambda: client_a.call("lookup_ticket", {"uri": "ticket://1001"}, token), attempts=1)
        except (TransientError, CircuitOpenError):
            pass
    assert client_a.breaker.state is BreakerState.OPEN
    degraded_path = gateway.call_with_fallback(["tickets-a", "tickets-b"], "lookup_ticket", {"uri": "ticket://1001"}, token)
    assert degraded_path["isError"] is False
    assert degraded_path["upstream_token_prefix"].startswith("ups.tickets-b")
    human = gateway.call_with_fallback(["tickets-a"], "lookup_ticket", {"uri": "ticket://1001"}, token)
    assert human["degraded"] is True
    assert worm.rows and worm.rows[-1]["digest"]
    print(json.dumps({"ok": True, "correlation_id": cid, "worm": len(worm.rows), "breaker": client_a.breaker.state.value}))


if __name__ == "__main__":
    _demo()
```

Graceful degradation contract: open circuit on `tickets-a` skips that client (Cursor isolation); `tickets-b` serves the same allowlisted read; if the whole chain is open/permanent, the host returns structured `isError` + `escalate_to_human` so the model/parser does not spin. Inbound MCP Bearer is checked for `aud` and **never** copied into `upstream_token_prefix`. `requestState` is HMAC-bound and single-use. Allowlist is fail-closed (unlike Cursor empty=all). `cacheScope: public` is refused for token-filtered catalogs.

---

## 6. Architectural System Design Scenarios

Exactly two enterprise designs. Numbers and component choices are from the research file.

### Scenario 1 — Internal knowledge agent (read-mostly, regulated)

**Problem statement.** Multi-tenant internal knowledge agent over wiki + ticket search (`resources/*` + a handful of search tools). Success metric from research S1: **zero cross-tenant `public` cache hits**. Write tools (email, GitHub) must not share the catalog. Catalog can grow past **~1–5%** of the context window (80 tools × 350 tokens = **28k** descriptors → **$0.140/turn** uncached Sol/Opus, **$0.014** cache-read). Compliance: audience-bound tokens, no passthrough, PII redact before the model sees `resources/read`. A platform team wants N direct Cursor-style sockets; a vendor wants Anthropic-hosted connectors (egress from Anthropic IPs). HITL skip is proposed “for p95.”

**Proposed architecture.**

```
┌────────────┐     ┌───────────────────────────────────────────────────────────┐
│ Slack /    │ SSE │ CONTROL PLANE                                             │
│ IDE host   │────▶│ Gateway: SSO/EMA, RFC 8707 aud, Mcp-Name WAF, Origin 403  │
└────────────┘     │ Policy: PII detect→redact→audit; cacheScope=private       │
                   │          annotations ignored for authz                    │
                   │ Catalog: allowlist ≤ search tools; hash-pin; list_changed │
                   │          = re-review; progressive discovery if >1–5% ctx  │
                   │ Orchestrator: Temporal workflow; Activities=tools/call    │
                   │ HITL on any remaining write; no GitHub in this catalog    │
                   └────┬──────────────────────────────┬───────────────────────┘
                        │ POST /mcp + headers          │ resources/read
                        ▼                              ▼
                   ┌──────────────────┐        ┌──────────────────────────────┐
                   │ DATA PLANE       │        │ TOOL PROXIES                 │
                   │ Streamable HTTP  │        │ Wiki/ticket APIs             │
                   │ MCP servers      │        │ NEW OBO token; no passthrough│
                   │ JSON or SSE/req  │        │ sanitize outputs; file://    │
                   │ Tasks if long    │        │ path check                   │
                   └────────┬─────────┘        └──────────────┬───────────────┘
                            │                                 │
                            ▼                                 ▼
                   ┌───────────────────────────────────────────────────────────┐
                   │ PERSISTENCE / TELEMETRY                                   │
                   │ Handles in args; taskId; HMAC requestState nonce store    │
                   │ Kafka outbox intent→result; WORM: Mcp-Name, catalog hash  │
                   └───────────────────────────────────────────────────────────┘
```

**Technology choices.** Enterprise MCP gateway (Microsoft / Envoy `MCPRoute` include-filter / Cloudflare Workers stateless) as one `aud`. `cacheScope: private`. Inject resources as **untrusted**. Progressive discovery over dumping 28k tokens. Prefer first-party `https://mcp.<vendor>.com` over aggregator proxies. stdio only for laptop-local files under OS sandbox — not for this shared knowledge plane. Avoid: mixing email/GitHub into the same prompt as wiki search (tool-authority deputy); `require_approval: never` on day one; marking filtered lists `public`.

**Trade-off evaluation matrix.**

| Dimension | A. Direct host→N servers (Cursor-native) | B. Recommended: MCP gateway + private cache + progressive discovery + Temporal Activities | C. Provider-hosted MCP client (Anthropic/OpenAI) |
| --- | --- | --- | --- |
| Cost | 28k descriptors **$0.140/turn** uncached if every server’s `tools/list` is dumped; N OAuth dances | `allowed_tools` + cache-read **$0.014/turn** descriptors; protocol **$0**/1k; 1k×400-token calls **[inferred] ~$2 + output** on Sol | Same token class + provider `tools/list` RTT; still **$0** MCP SKU; Anthropic **tools only** (no resources) |
| Latency | No extra hop; p95 = max(slow server) + approval UI **[inferred]** | Extra gateway hop ≪ upstream 50–2000 ms; Envoy steers 429 backends; SSE buffering is the real p95 if `X-Accel-Buffering` missing | Hosted extra hop (cookbook: runtime `tools/list` then model); approval UX is theirs |
| Ops | Catalog explosion; prefix collisions; N breakers in every IDE | One `POST /mcp` router; `ttlMs` 300000; hash-pin; multi-router instances | No local client; cannot pin your own catalog hash as easily; OpenAI list may not refetch while in context (rug-pull **or** stale-good) |
| Security | Shadowing across servers in **one prompt**; N consent surfaces | One `aud`; no passthrough; `cacheScope: private`; WORM on `Mcp-Name`; gateway is itself a deputy — must not passthrough | Egress to vendor **then** server; ZDR stops at MCP hop; claude.ai cannot hit VPC MCP |
| Scalability | <15 trusted servers, developers (research 6.2) | Many teams behind one policy; RPM/WAF on headers; Tasks for long reads | OpenAI MCP RPM **200–2000**; not a VPC scale path for Anthropic remote |

**Decision rationale.** **B** is the only option that hits S1’s success metric (no `public` cache), keeps writes out of the knowledge catalog, and treats descriptor tokens as a first-class NFR (`allowed_tools` / progressive discovery). A is the documented fit for **<15 trusted servers** on developer laptops, not a regulated multi-tenant agent. C wins for product agents that already accept vendor egress; it fails the VPC/resources requirement (Anthropic connector is tools-only; claude.ai needs public internet). Interview close: “One audience, private cache, untrusted resources, no GitHub in this prompt.”

### Scenario 2 — Multi-agent platform (A2A mesh, MCP leaves)

**Problem statement.** Cross-org due-diligence platform: Shop-Manager analogue talks to customer/supplier **agents**; each specialist needs scanners/manuals/lifts (MCP tools). A PM wants one mega-`tools/call` that “is” the partner agent (flatten A2A into MCP). Another wants Foundry Toolbox so every intern hits **one** MCP URL with 400 tools. Orchestrator must not load 400 tools (28k×N descriptors; cache bust on any list change). Long partner work must survive 60 s gateway timeout. Two auth stacks (A2A + MCP OAuth) are on the table.

**Proposed architecture.**

```
┌────────────┐     ┌───────────────────────────────────────────────────────────┐
│ Analyst UI │ SSE │ CONTROL PLANE                                             │
│ / A2A in   │────▶│ Gateway: workforce SSO; A2A AUTH vs MCP RFC 8707 split    │
└────────────┘     │ Policy: per-specialist allowlist; no shared tool prompt   │
                   │ Router: A2A to opaque peers; MCP only inside each agent   │
                   │ Orchestrator: Temporal; A2A SendMessage=Activity;         │
                   │               MCP tools/call=Activity; Tasks for long MCP │
                   │ Cap: never wrap a multi-turn A2A task as blocking MCP     │
                   └────┬──────────────────────────┬───────────────────────────┘
                        │ A2A task lifecycle       │ MCP tools/call
                        ▼                          ▼
                   ┌──────────────────┐    ┌───────────────────────────────────┐
                   │ A2A PEER PLANE   │    │ MCP TOOL PLANE (leaves)           │
                   │ Agent Card       │    │ Mechanic: scanner/manual/lift     │
                   │ artifacts        │    │ each specialist = own client(s)   │
                   │ contextId+taskId │    │ Streamable HTTP + OAuth; stdio    │
                   │ opaque CoT       │    │ only for that agent’s local hw    │
                   └────────┬─────────┘    └─────────────────┬─────────────────┘
                            │                                │
                            ▼                                ▼
                   ┌───────────────────────────────────────────────────────────┐
                   │ PERSISTENCE                                               │
                   │ A2A task immutable; MCP handles/taskId; Kafka: a2a vs mcp │
                   │ topics separate; WORM both planes; no token passthrough   │
                   └───────────────────────────────────────────────────────────┘
```

**Technology choices.** Official A2A line: *MCP inside the agent, A2A between agents*. Optional Foundry Toolbox **only** as a **curated** bundle for one specialist, not 400 tools into the orchestrator. Long jobs: Tasks extension (or A2A task), not blocking `tools/call`. Skill **MAY** be re-exposed as a **stateless** MCP tool; multi-turn negotiation stays A2A. Avoid: N direct MCP connections from every agent (research 6.1); STDIO-only if Claude.ai/ChatGPT must consume the SaaS.

**Trade-off evaluation matrix.**

| Dimension | A. Flatten partner agent as one MCP `tools/call` | B. Recommended: A2A mesh + per-specialist MCP leaves + Tasks; Toolbox only as a curated leaf | C. Foundry Toolbox / one MCP URL with the union of 400 tools |
| --- | --- | --- | --- |
| Cost | Blocking call holds the LLM turn; retries re-bill thinking; no Agent Card reuse | Orchestrator loads **routing** tools only; specialists pay their own descriptor tax; MCP SKU **$0**; A2A has its own token path | 400-tool dump blows the 1–5% rule (28k already **$0.140/turn** at 80 tools); cache invalidation on any leaf `list_changed` |
| Latency | Proxy timeout on long negotiation; no Task poll | A2A task + MCP Tasks survive 60 s gateways; p99 = poll/notify not SSE hold **[inferred]** | One RTT to Toolbox then fan-out; include/exclude filters help but orchestrator still sees a fat catalog unless filtered hard |
| Ops | One protocol to debug (false simplicity) | Two auth stacks; Agent Card sprawl — documented cost of org boundaries | One URL; Toolbox/gateway is a **deputy**; must implement per-client consent / no passthrough |
| Security | Partner CoT and tools leak into your MCP token; confused deputy on the mega-tool | Opaque peers; each leaf has its own `aud` and allowlist; prompt isolation **per specialist** (process isolation ≠ prompt isolation if you merge catalogs) | Union catalog = shadowing + implicit poisoning (MCP-ITP) across domains; one stolen Toolbox token is the whole estate if `aud` is shared |
| Scalability | Cannot represent A2A artifacts / immutable tasks | Horizontal specialists; Envoy MCPRoute per leaf; A2A for cross-org | Horizontal routers yes; **semantic** scale fails when 400 tools share one prompt |

**Decision rationale.** **B** is the official complementary pattern and the only one that keeps long partner work on a **task lifecycle** instead of a blocking JSON-RPC call. A violates “do not flatten multi-turn agent work into `tools/call`.” C is the right **leaf** packaging (Foundry catalogs MCP, OpenAPI, **and** A2A as distinct types) and the wrong **orchestrator** catalog. Interview close: “A2A at the org edge. MCP at the metal. Never load 400 tools into the lead. Tasks, not 60-second POST.”

---

*End of module. Six sections. Five topics (tools, resources, MCP servers, MCP clients, interoperability). Token `$ / 1k` tables are from vendor list prices dated 2026-08-21 plus research worked examples; MCP protocol fee is **$0**. No unpublished MCP e2e p50/p95/p99 SLOs — percentiles are labeled **[inferred]** or bound from TLS/JSON-RPC/upstream/model fragments. OpenAI Fast 80 tok/s / 99.9% is the LLM plane.*
