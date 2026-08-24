# Research: MCP & Interoperability - Tools, Resources, Servers, Clients

**Date researched**: 2026-08-21
**Sources consulted**: 40

## Scope and evidence labels

This brief covers the Model Context Protocol (MCP) revision `2026-07-28`, the current generally available revision on the research date, and calls out legacy behavior where it remains operationally relevant. MCP is a host-mediated protocol for connecting an AI application to capability servers; it is not an agent runtime, a workflow engine, or an agent-to-agent task protocol. The requested tools, resources, servers, and clients are covered across all six mandatory research dimensions. `[inferred]` marks design guidance derived from the cited protocol and systems evidence rather than a normative MCP requirement. Research measurements retain their original datasets and do not imply ecosystem-wide rates.

## 1. System Topology & Mechanics

### The actual boundary

The core topology is:

```text
user
  |
MCP host (AI application: owns model loop, consent UI, policy, context)
  |-- MCP client A -- stdio ---------- local server A -- filesystem/API
  |-- MCP client B -- Streamable HTTP remote server B -- SaaS/database
  `-- MCP client C -- Streamable HTTP gateway -------- many backends
```

An MCP **host** is the AI application. It decides which servers to trust, which tools and resources enter model context, whether a model-proposed tool call may execute, and how results return to the model. An MCP **client** is the protocol component that speaks to one server. An MCP **server** publishes capabilities. The official server concepts classify tools as model-controlled actions, resources as application-controlled context, and prompts as user-controlled templates. [[3]](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/docs/2026-07-28/learn/server-concepts.mdx)

MCP messages use UTF-8 JSON-RPC 2.0 requests, responses, errors, and notifications. JSON-RPC supplies correlation IDs and standard errors, but it does not define MCP capabilities, authorization, delivery guarantees, or business semantics. [[2]](https://www.jsonrpc.org/specification) The modern core permits client-to-server requests and notifications plus server responses and request-scoped or subscribed notifications; servers no longer initiate standalone JSON-RPC requests. [[4]](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/basic/index.mdx)

`[inferred]` Treat MCP like an integration boundary, not a trust boundary. Standard framing makes unlike implementations interoperable; it does not make a server, its metadata, its returned content, or the model's choice trustworthy.

### The 2026-07-28 lifecycle

The current revision is deliberately stateless at the protocol core:

1. A client may call `server/discover` to learn supported versions, server capabilities, identity, instructions, and cache hints. Servers must implement discovery, but clients may skip it and send a functional request directly. Server identity is self-reported and must not drive security decisions. [[5]](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/server/discover.mdx)
2. Every request carries `io.modelcontextprotocol/protocolVersion` and `io.modelcontextprotocol/clientCapabilities` in `params._meta`; `clientInfo` is recommended, not an authenticated identity. Every successful modern result includes `resultType`, normally `complete` or, on supported methods, `input_required`. [[4]](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/basic/index.mdx) The canonical schema is the machine-readable contract for generated types and conformance validation. [[17]](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/schema/2026-07-28/schema.json)
3. There is no `initialize` / `notifications/initialized` handshake and no `Mcp-Session-Id`. Any request can reach any compatible replica. Cross-call application state must be explicit, normally a server-minted handle passed as an ordinary argument. [[1]](https://blog.modelcontextprotocol.io/posts/2026-07-28/)
4. `tools/call`, `resources/read`, and `prompts/get` can return `input_required`. The client gathers elicited user data, a model sample, or roots information, then retries the original method with a new JSON-RPC ID, exact opaque `requestState`, and `inputResponses`. [[11]](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/basic/patterns/mrtr.mdx)
5. A client that wants change events opens `subscriptions/listen` with an explicit filter. The server acknowledges a subscription ID and must not emit event types the client did not request. [[12]](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/basic/patterns/subscriptions.mdx)

This differs materially from revisions `2024-10-07` through `2025-11-25`, which form the SDKs' **legacy era**: `initialize` negotiates a connection, HTTP can allocate a session, and servers may make requests over an open channel. The official TypeScript SDK can probe with `server/discover` in `versionNegotiation: { mode: 'auto' }`, then fall back to the legacy handshake; pinning `2026-07-28` fails instead of downgrading. On stdio, its probe may spawn a disposable sibling process because an old server may wait indefinitely for `initialize`. [[19]](https://ts.sdk.modelcontextprotocol.io/v2/protocol-versions)

`[inferred]` Record the negotiated era, revision, SDK version, and enabled extensions in every connection trace. A deployment that supports both eras is maintaining two lifecycle, state, notification, and authorization postures behind one apparent API.

### Tools: actions offered to the model

Tools are named, schema-described operations. A server declares the `tools` capability, a client discovers definitions with paginated `tools/list`, and calls one with `tools/call`. A definition contains a name, description, JSON Schema `inputSchema`, optional `outputSchema`, icons, and optional behavioral annotations. In `2026-07-28`, tool schemas use JSON Schema 2020-12; input schemas retain an object root while output schemas and `structuredContent` may be any JSON value. [[6]](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/server/tools.mdx) [[16]](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/changelog.mdx) Draft 2020-12 supplies composition, conditionals, and reusable definitions, but schema evaluation must still be bounded against pathological depth or references. [[39]](https://json-schema.org/draft/2020-12)

Tool results may contain typed content blocks, structured content, embedded resources, resource links, and an `isError` flag. Distinguish two error planes:

- **Protocol error**: malformed request, unknown method/tool, unsupported version, header mismatch. The client receives a JSON-RPC error and ordinarily cannot treat the tool as having executed successfully.
- **Tool execution error**: the tool ran but the domain operation failed. The normal tool result has `isError: true`, allowing the model or host to inspect a recoverable domain message.

Tool annotations such as read-only, destructive, idempotent, or open-world behavior are hints, not enforcement. The specification requires clients to treat annotations as untrusted unless the server itself is trusted, and recommends a human able to deny invocations. [[6]](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/server/tools.mdx)

`[inferred]` Make each production tool narrow and outcome-oriented. Validate all arguments at the handler boundary, return machine-readable domain errors, declare a bounded output schema, and enforce authorization from verified identity and policy rather than the description or annotation. Separate `invoice.read` and `invoice.refund` rather than putting a free-form `operation` switch behind one broadly privileged tool.

### Resources: context selected by the application

Resources expose application-selected data through URIs. A server declares the `resources` capability; clients use `resources/list`, `resources/templates/list`, and `resources/read`. Direct resources have fixed URIs. Templates use parameterized URI templates and can be paired with completion. A read may return several text or base64 binary content items, each with URI and optional MIME type. [[7]](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/server/resources.mdx)

Resources are not inherently files and a custom URI scheme is not a network instruction. The server decides how a URI maps to data. For `https://` resources the specification permits direct client retrieval when appropriate; for `file://`, servers must sanitize paths and prevent traversal. Resource metadata can include byte size, audience, priority, and last-modified hints. These are useful for host-side selection and token budgeting, but they do not prove content safety or freshness. [[7]](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/server/resources.mdx)

Use a **resource** when the host should control retrieval, filtering, display, and context inclusion. Use a **tool** when the operation performs computation, changes state, needs model-selected parameters, or requires an audited action. A search query is usually a tool; an identified search result or schema document can be a resource. Do not disguise writes as resource reads.

### Servers and clients

A server is a protocol adapter around existing domain logic. It should not duplicate the AI host's reasoning loop. The current TypeScript SDK registers tools/resources/prompts with typed handlers and can serve local stdio or remote HTTP; its v2 server and client packages are separate. [[18]](https://ts.sdk.modelcontextprotocol.io/v2/) Official SDK tiering is based on feature completeness, conformance, maintenance, and documentation: as of the research date, TypeScript, Python, C#, and Go are Tier 1; Java, Rust, and Ruby are Tier 2; Swift, PHP, and Kotlin are Tier 3. Experimental extensions are not required for tier status. [[22]](https://modelcontextprotocol.io/community/sdk-tiers)

A host commonly maintains one client per configured server, normalizes colliding names, filters capabilities, and translates tool definitions to its model provider's tool format. Tool names are unique only within a server; aggregated clients need stable namespacing rather than relying on non-unique self-reported `serverInfo`. [[6]](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/server/tools.mdx)

MCP interoperability can also be provided by a model platform rather than a client the application runs itself. For example, the OpenAI Responses API accepts MCP tools backed by custom remote servers or predefined connectors and exposes tool-choice controls. [[34]](https://developers.openai.com/api/reference/cli/resources/responses/methods/create) `[inferred]` Hosted connectors reduce client-loop code but move transport, catalog retrieval, approval, data retention, and failure visibility into the platform contract; verify those controls separately from MCP wire compliance.

Representative modern TypeScript server pattern:

```ts
import { McpServer } from "@modelcontextprotocol/server";
import { serveStdio } from "@modelcontextprotocol/server/stdio";
import * as z from "zod/v4";

serveStdio(() => {
  const server = new McpServer({ name: "billing", version: "1.0.0" });
  server.registerTool(
    "invoice.get",
    {
      description: "Read one invoice visible to the authenticated principal",
      inputSchema: z.object({ invoiceId: z.string().min(1) })
    },
    async ({ invoiceId }) => ({
      content: [{ type: "text", text: await readAuthorizedInvoice(invoiceId) }]
    })
  );
  return server;
});
```

The SDK validates schema-shaped input before the handler, but the handler still owns tenant authorization, business invariants, output limits, timeouts, and audit records. [[18]](https://ts.sdk.modelcontextprotocol.io/v2/)

Representative dual-era client pattern:

```ts
import { Client, StreamableHTTPClientTransport } from "@modelcontextprotocol/client";

const client = new Client(
  { name: "enterprise-host", version: "1.0.0" },
  { versionNegotiation: { mode: "auto" }, cachePartition: principalId }
);
await client.connect(new StreamableHTTPClientTransport(new URL(serverUrl)));
const { tools } = await client.listTools();
const result = await client.callTool({
  name: "invoice.get",
  arguments: { invoiceId: "inv_123" }
});
```

The TypeScript client honors modern cache hints, partitions private cache entries, validates output schemas, and can automatically fulfill bounded `input_required` rounds. Those SDK conveniences do not replace host policy or user consent. [[20]](https://ts.sdk.modelcontextprotocol.io/v2/api/%40modelcontextprotocol/client/client/client.html)

### Transport bindings

| Binding | Mechanics | Best fit | Operational boundary |
|---|---|---|---|
| stdio | Host spawns subprocess; newline-delimited JSON-RPC over stdin/stdout; logs on stderr | local IDE/desktop tools, single-user adapters | OS process, package, environment, filesystem and network permissions |
| Streamable HTTP | POST each message to one endpoint; response is JSON or request-scoped SSE; subscribed events use a long-lived SSE response | remote shared services, SaaS, gateways, horizontal scale | TLS, OAuth, HTTP gateway, tenant isolation, rate limits |
| custom | Must preserve MCP JSON-RPC patterns and per-request metadata | constrained runtimes or established internal buses | custom interop and security burden |

The stdio server must write only valid MCP messages to stdout; arbitrary output corrupts framing. The client owns the child lifecycle, while stderr is an unstructured logging channel. [[9]](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/basic/transports/stdio.mdx)

Modern Streamable HTTP has no session-affinity requirement. Each POST carries `MCP-Protocol-Version`, `Mcp-Method`, and, for calls/reads/gets, `Mcp-Name`; selected tool properties annotated `x-mcp-header` can be mirrored as `Mcp-Param-*`. Servers processing the body must reject missing or mismatched mirrored values with HTTP 400 / `-32020 HeaderMismatch`. A response SSE stream may carry progress before the final response. Resumable SSE using `Last-Event-ID` is not supported. [[10]](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/basic/transports/streamable-http.mdx)

Legacy HTTP+SSE is deprecated. Roots, protocol sampling, and protocol logging are also deprecated with at least the specification's twelve-month deprecation window; current guidance is explicit tool/resource configuration, direct model-provider integration, and OpenTelemetry or stderr respectively. [[16]](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/changelog.mdx)

### MCP versus adjacent interoperability

MCP standardizes agent/host-to-tool-and-context access. A2A standardizes communication with a remote agent that has identity, skills, messages, task state, artifacts, and asynchronous completion. The A2A project explicitly describes the protocols as complementary: use MCP inside an agent to reach capabilities and A2A between independent agents for delegation. [[33]](https://a2a-protocol.org/latest/)

`[inferred]` Use a normal typed API directly when only one application owns both sides and no portable AI capability catalog is needed. Use MCP when multiple compatible hosts should discover and invoke the same model-facing capability. Use A2A when the remote party owns planning and a durable task lifecycle rather than one bounded function.

## 2. Token Economics & NFR Metrics

### Cost model

MCP itself charges nothing and defines no model price. The economic cost comes from catalog metadata and returned content entering model context, extra protocol round trips, underlying tool/API work, host/model inference, and platform operations:

```text
C_run = C_discover_and_catalog
      + C_model_input(tool schemas + selected resources + history)
      + C_model_output
      + sum(C_tool_backend + C_tool_result_tokens)
      + C_auth + C_gateway + C_server_compute
      + C_MRTR + C_retries + C_human_review

L_run = L_auth/discovery/list
      + sum_on_critical_path(L_model + L_tool + L_MRTR)
      + L_queue/retry
```

A large tool catalog creates two distinct taxes: listing traffic and repeated schema tokens sent to the model. Modern list results require `ttlMs` plus `cacheScope` (`public` reusable across authorization contexts, `private` only within the same context), and stable deterministic ordering improves catalog caching and upstream prompt-cache stability. [[16]](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/changelog.mdx) Cache hints also cover prompts, resources, resource templates, reads, and discovery. A TTL is a freshness permission, not a guarantee that an intermediary or host cached the value.

`[inferred]` Measure catalog bytes and **model tokens after host transformation**, not only tool count. Give the model the smallest authorized, task-relevant subset. Prefer several coherent servers or a policy-aware catalog gateway to a flat catalog of hundreds of unrelated actions, but ensure routing cannot hide a necessary tool.

### Metrics that matter

| Plane | Metric | Why it matters |
|---|---|---|
| interoperability | version/era negotiation success; conformance pass rate; method/schema compatibility | portable behavior, not merely successful TCP connection |
| catalog | tools/resources visible per principal; list bytes/tokens; cache hit and invalidation rate | context cost and stale capability risk |
| execution | valid-call rate; tool success; protocol error by code; duplicate-side-effect rate | separates model selection, protocol, and domain failures |
| latency | discover/list/call/read p50/p95/p99; first progress; MRTR rounds; backend time | locates critical-path delay |
| security | denied call rate; approval rate; scope elevation; schema/hash changes; cross-server flow alerts | detects abuse and permission drift |
| reliability | cancellation completion; timeout; retry; subscription reconnect; replica skew | tests distributed behavior |
| economics | input/output tokens, backend charge, server compute, cost per verified task | prevents cheap transport from hiding expensive context/action loops |

Use task success as the numerator. `Cost / successful tools/call` can reward unnecessary tool use; `cost / verified user outcome` is harder to game.

### Caching, pagination, batching, and routing

List methods support cursors and modern SDKs may auto-aggregate pages. Cache separately by canonical server endpoint, negotiated revision, authorization context, and request parameters. A private response must never cross principals. The TypeScript v2 client defaults its in-memory cache to a bounded 512 entries and allows `use`, `refresh`, or `bypass`; these are SDK defaults, not protocol-wide requirements. [[20]](https://ts.sdk.modelcontextprotocol.io/v2/api/%40modelcontextprotocol/client/client/client.html)

Streamable HTTP header mirroring lets a gateway route, meter, and authorize by method/name without parsing arbitrary JSON, provided it verifies the revision and header/body agreement. [[10]](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/basic/transports/streamable-http.mdx) JSON-RPC defines batch messages generally, but the current standard transport pages specify each modern HTTP message as its own POST. Do not assume a host/server pair supports application batching unless its conformance and SDK documentation say so.

> ⚠️ Limited public data available for this dimension. The MCP specification and official SDKs define wire behavior and cache controls, but there is no normalized public benchmark for catalog-token overhead, p95 tool latency, maximum sustainable calls per second, cache-hit rate, or total cost across representative MCP hosts and servers. Capacity and SLO values must come from workload-specific load tests.

## 3. Distributed Resilience & State

### Explicit state ownership

Modern MCP removes transport session state, not application state. `[inferred]` Assign each state class deliberately:

| State | Owner | Durability |
|---|---|---|
| conversation, model context, approvals | host | host store, privacy lifecycle |
| negotiated era/version and discovery cache | client/host | cache bounded by authorization context and TTL |
| cross-call business workflow | domain service | durable database/workflow engine |
| MRTR continuation | server-minted `requestState` plus durable backend if single-use | integrity-protected, principal-bound, expiring |
| notification interest | client subscription stream and server event bus | reconnect/re-subscribe policy |
| tool/resource catalog | server source of truth; client cache | versioned definitions plus invalidation |
| OAuth tokens | client secure store | expiry, rotation, revocation |

For MRTR, the server may encode continuation in opaque `requestState`, but the spec requires it to be treated as attacker-controlled. If it affects authorization or business logic, protect integrity; bind principal, expiry, and originating request to prevent cross-user/cross-request replay. Strict one-time operations still need server-side consumption state. [[11]](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/basic/patterns/mrtr.mdx)

### Retries and idempotency

JSON-RPC IDs correlate requests; they are not business idempotency keys. A client can time out after a remote write committed but before receiving the result. Blindly retrying `payments.create` may execute twice. MCP does not give `tools/call` exactly-once semantics.

`[inferred]` For every side-effecting tool:

1. accept a host- or server-issued idempotency key in the input schema;
2. persist `(principal, tool, idempotency_key) -> terminal result` at the domain boundary;
3. return the prior result on a duplicate with the same canonical arguments;
4. reject key reuse with different arguments;
5. expose a separate status/read tool for ambiguous timeouts;
6. retry only known transient transport and dependency failures with bounded backoff and jitter.

Do not infer retry safety from a tool's `idempotentHint`; annotations are untrusted metadata. Cancellation is also cooperative: stdio sends `notifications/cancelled`, while HTTP closes that request's response stream; the server should stop work as soon as practical, but cancellation cannot undo a committed external side effect. [[8]](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/basic/transports/index.mdx)

### Horizontal scale and long work

Stateless requests allow ordinary round-robin load balancing with no shared MCP session store. That benefit disappears if a handler assumes process-local continuation, authorization, or subscription state. Externalize durable domain state and use an inter-replica event bus for `subscriptions/listen`. The official Python v2 SDK emphasizes that modern HTTP requests can reach any replica and can serve modern and legacy clients from the same application. [[21]](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/whats-new.md)

For long-running operations, do not hold an ordinary call indefinitely by default. The current Tasks capability is an extension rather than core MCP: servers can return a task handle and clients poll/update/cancel through the negotiated extension. [[1]](https://blog.modelcontextprotocol.io/posts/2026-07-28/) `[inferred]` Put durable scheduling, leases, checkpoints, and retries in a workflow/queue system; expose start/status/cancel through MCP and make the business task ID visible.

### Notifications are hints, not a database log

`subscriptions/listen` supplies a filtered long-lived stream. It has acknowledgment and correlation, but Streamable HTTP explicitly does not support `Last-Event-ID` resumability. [[10]](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/basic/transports/streamable-http.mdx) A dropped connection can therefore miss changes.

`[inferred]` On reconnect, re-open the subscription and refresh the relevant list/resource instead of assuming an uninterrupted event history. Use notifications to invalidate caches; use the server's current list/read result as truth. Include keep-alive comments and disable reverse-proxy buffering for SSE as the spec recommends.

### Compatibility rollout

Use a compatibility matrix in CI:

```text
client eras: modern pinned | auto dual-era | legacy only
server eras: modern only   | dual-era      | legacy only
transports:  stdio         | HTTP JSON     | HTTP SSE response/subscription
auth:        unauth local  | user OAuth    | machine extension
```

Run the official Inspector CLI in CI to list/call/read and use SDK conformance tests rather than treating successful discovery as full compatibility. Inspector has stable machine-readable output and failure classes for auth, reachability, and tool errors. [[23]](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/docs/2026-07-28/tools/inspector/cli.mdx)

`[inferred]` During migration, canary a modern-only endpoint or version pin first. Track fallback rate. A silent fallback can restore connectivity while removing MRTR, modern cache, header routing, or stateless assumptions that the application depends on.

## 4. Enterprise Security & Governance

### Threat model by boundary

MCP composes conventional application vulnerabilities with semantic attacks on the model:

- **server/package compromise**: local code runs with host privileges; remote code acts with its service identity;
- **tool/schema poisoning**: names, descriptions, schemas, annotations, or returned content instruct the model to misuse this or another server;
- **indirect prompt injection**: a resource, webpage, ticket, or database row contains hostile instructions;
- **confused deputy**: a server or host uses stronger credentials than the requesting principal should have;
- **argument injection**: model-generated strings reach shell, SQL, filesystem, URL fetch, or templates unsafely;
- **cross-server exfiltration**: content obtained from a sensitive server becomes an argument to a public or attacker-controlled tool;
- **OAuth attacks**: token theft/passthrough, audience confusion, authorization-server mix-up, redirect abuse, malicious metadata fetch;
- **availability abuse**: oversized schemas/results, recursion, expensive tools, notification floods, held streams, or unbounded MRTR.

The OWASP MCP cheat sheet recommends per-server least privilege, strict schema and input/output validation, sandboxing, explicit confirmation for sensitive actions, supply-chain controls, and cross-server isolation. [[27]](https://cheatsheetseries.owasp.org/cheatsheets/MCP_Security_Cheat_Sheet.html)

### Authentication and authorization

MCP authorization is optional. When used over HTTP, the current spec makes the MCP server an OAuth protected resource and the MCP client an OAuth client. stdio should obtain credentials from its environment rather than apply the HTTP OAuth flow. Protected Resource Metadata (RFC 9728) discovers authorization servers; clients support OAuth Authorization Server Metadata or OpenID Connect discovery; Client ID Metadata Documents are preferred, while Dynamic Client Registration is deprecated for backward compatibility. [[13]](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/basic/authorization/index.mdx) [[36]](https://www.rfc-editor.org/rfc/rfc9728.html)

Critical requirements include:

- include RFC 8707 `resource` in authorization and token requests and validate that a token is intended for this MCP server; [[35]](https://www.rfc-editor.org/rfc/rfc8707.html)
- never pass the inbound MCP token unchanged to an upstream API; obtain a separate upstream token;
- use PKCE, require advertised support, prefer/require `S256` where technically capable, use exact redirect validation, HTTPS except localhost redirects, and secure token storage; [[14]](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/basic/authorization/security-considerations.mdx)
- validate authorization response issuer per RFC 9207 to prevent mix-up, and bind registered credentials to the issuing authorization server; [[37]](https://www.rfc-editor.org/rfc/rfc9207.html)
- begin with minimal scopes and use targeted `WWW-Authenticate` step-up challenges rather than granting every advertised capability initially. [[15]](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/docs/2026-07-28/tutorials/security/security_best_practices.mdx)

OAuth proves an authenticated authorization context; it does not automatically implement per-tool or row-level authorization. `[inferred]` Map verified claims to a server-side policy decision on every call/read: `(principal, tenant, tool/resource, arguments, risk, requested scopes)`. Never trust `clientInfo`, server name, model text, or a tenant ID supplied only in arguments.

Machine-to-machine access is not the same as delegated user authorization. MCP defines an optional OAuth Client Credentials extension for automated systems. Negotiate it explicitly and give workload identities narrower tools/scopes than interactive users. [[40]](https://modelcontextprotocol.io/extensions/auth/oauth-client-credentials)

### Local server and supply-chain governance

stdio is local but not low risk: installing a package and allowing the host to spawn it can grant code execution with the user's files, environment variables, credentials, and network. The official Registry is a metadata directory, not a curated security authority. It authenticates namespaces and points to npm/PyPI/container/remote artifacts, while delegating code scanning to package registries and downstream aggregators. [[24]](https://modelcontextprotocol.io/registry/about) Its moderation policy explicitly says consumers should assume minimal moderation and that vulnerable or low-quality servers generally are not removed. [[25]](https://modelcontextprotocol.io/registry/moderation-policy)

`[inferred]` Enterprise installation policy should require:

1. allowlisted publisher namespace and repository;
2. exact artifact version and digest, lockfile/SBOM, signature/provenance where available;
3. static and dependency scan plus review of the full tool schema and server command;
4. sandbox profile: read-only filesystem by default, explicit mounts, no inherited secret environment, denied network unless required, CPU/memory/process/time limits;
5. approved configuration schema and secret references from a credential broker;
6. recorded tool/resource definition digest and alert/reapproval on change;
7. staged promotion and emergency kill switch.

Published registry versions are immutable and unique, which helps provenance, but the registry's recommended semantic version remains metadata rather than executable integrity. [[26]](https://modelcontextprotocol.io/registry/versioning)

### Semantic content and approval

All text from a server is untrusted data, including server instructions, tool descriptions, errors, resources, and tool results. Strict JSON Schema prevents type confusion but cannot prove a natural-language field is benign. Keep system policy outside server-controlled context, label provenance, limit server content, prevent one server from altering another's policy/name, and implement data-flow controls before sensitive text becomes an argument to an external tool.

Research substantiates the concern but should not be generalized beyond each experiment. MCPTox built attacks using 45 live servers and 353 tools to study malicious instructions in tool metadata. [[30]](https://arxiv.org/abs/2508.14925) MCP Security Bench reports 12 attack classes, nine agents, 10 domains, 400+ tools, and 2,000 instances across planning, invocation, and response handling. [[31]](https://arxiv.org/abs/2510.15994) An empirical server study reported 7.2% general vulnerabilities and 5.5% MCP-specific tool poisoning in its collected dataset; these are historical sample measurements, not current prevalence estimates. [[32]](https://arxiv.org/abs/2506.13538)

Approval must occur in trusted host UI, after policy evaluation, showing the actual tool, destination, principal, consequential parameters, and expected effect. MRTR elicitation helps a server request confirmation or missing data but does not prove that the host presented an honest dialog or that the operation is authorized.

### Incidents demonstrate composition risk

The official Inspector had a high-severity XSS path in versions before `0.16.6`: a malicious remote server redirect could lead through the built-in proxy to command execution. [[28]](https://github.com/advisories/GHSA-g9hg-qhmf-q45m) `mcp-remote` before its patched release had a critical OS command injection vulnerability when connecting to an untrusted server. [[29]](https://github.com/advisories/GHSA-6xpm-ggf7-wc3p) These advisories concern specific tooling and versions, not the core protocol, but illustrate how remote content plus a privileged local stdio launcher collapses boundaries.

### Audit and policy evidence

`[inferred]` Record, with redaction:

- authenticated subject, tenant, client/workload, server canonical endpoint and artifact digest;
- negotiated era/version/extensions and catalog digest;
- JSON-RPC correlation ID, host run/trace ID, tool/resource name, policy decision, approval identity;
- validated arguments hash plus permitted high-value fields, result/error class, latency, tokens, backend operation ID;
- scope challenges/grants, retries/idempotency key, MRTR rounds, cancellation and cache disposition;
- definition changes and server installation/configuration changes.

The modern spec documents W3C `traceparent`, `tracestate`, and `baggage` propagation in `_meta`; W3C Trace Context standardizes cross-service trace correlation. [[16]](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/changelog.mdx) [[38]](https://www.w3.org/TR/trace-context/) Never put tokens, secrets, raw PII, or unbounded tool output into trace baggage/logs.

## 5. Production Failure Modes

| Failure | Observable symptom | Root cause | Detection | Mitigation |
|---|---|---|---|---|
| era mismatch | `-32022`, 400, failed discovery, unexpected initialize | modern client vs legacy server or pin mismatch | era/fallback metric, compatibility tests | auto negotiation where safe; explicit pin for required features |
| malformed per-request metadata | `-32602` | missing version/capability `_meta` | protocol error counter | use current SDK; schema/conformance tests |
| header/body mismatch | HTTP 400, `-32020` | stale proxy, tampering, bad custom header encoding | gateway and server compare logs | enforce match; refresh tool schema before one bounded retry |
| method/capability contradiction | method-not-found after advertised support | replica/version skew or faulty server | correlate discovery/list/call by deployment | atomic rollout; capability contract tests |
| stale catalog | model calls removed/changed tool | overly long TTL or missed change stream | catalog digest and call errors | bounded TTL; invalidate on notification; refresh on schema mismatch |
| catalog/context explosion | high token cost, poor tool selection | too many verbose tools | schema bytes/tokens and selection accuracy | policy/task filtering; concise descriptions; split domains |
| name collision | wrong server/tool selected | aggregated servers both expose `search` | namespace collision check | stable host-assigned namespace; never trust server name uniqueness |
| invalid model arguments | validation error, no handler call | model output violates schema | invalid-call rate by field/model | tighter schema, examples/evals, bounded repair |
| duplicate side effect | two charges/messages | timeout followed by unsafe retry | idempotency collision and backend audit | domain idempotency and status query |
| cancellation after commit | client sees cancelled but action occurred | cooperative cancellation raced commit | backend operation ID | expose final status; communicate irreversible boundary |
| MRTR loop | repeated dialogs/cost | missing input, rejected input, server bug | round counter and repeated request fingerprint | maximum rounds/time; allow user abort; server validation |
| tampered/replayed `requestState` | cross-user continuation or repeated redemption | unsigned/unbound continuation | verification/replay failures | AEAD/HMAC, principal/request/expiry binding, one-time store |
| replica-local state | intermittent not-found after load balance | modern request hits another worker | replica-tagged traces | external state or explicit signed handle |
| missed update | stale resource after reconnect | subscription stream not resumable | gap/reconnect metric | re-list/re-read after reconnect |
| proxy-buffered SSE | progress/events arrive in bursts | reverse proxy buffering/idle close | first-event latency | `X-Accel-Buffering: no`, keep-alive, tuned idle timeout |
| stdout corruption | parser disconnect | local server logs/banner on stdout | invalid JSON frame | log only to stderr; protocol integration test |
| orphaned local process | CPU/memory leak after host exit | broken child lifecycle | process ownership monitoring | process group/job object, close/kill timeout |
| oversized result/schema | memory/context/latency spike | no output/pagination limit | byte/token limit counters | server and host caps, truncate/artifact link, pagination |
| resource traversal/SSRF | unauthorized file/internal URL access | URI/URL interpreted without allowlist | security tests and egress logs | canonicalize paths; scheme/host/IP allowlist; network sandbox |
| tool/result prompt injection | model leaks data or calls another tool | untrusted semantic content | canary/evals and cross-server flow rules | isolate provenance, output limits, policy gate, approval |
| token audience confusion | one server accepts another's token | missing RFC 8707 validation/passthrough | issuer/audience rejection metrics | resource-bound tokens; separate upstream token |
| scope escalation loop | repeated OAuth challenges | bad scope accumulation or denial cache | challenge loop metric | targeted scopes, remember denial, bounded retry |
| server rug pull | definition changes after approval | mutable remote catalog/package update | schema digest comparison | pin/review/reapprove; kill switch |
| local proxy RCE | remote data triggers local command spawn | web/proxy flaw plus privileged stdio | EDR, proxy audit | patch, isolate proxy, restrict commands, sandbox |
| silent downgrade | feature disappears without outage | auto fallback to legacy era | fallback rate and negotiated feature log | pin when modern semantics are required |

`[inferred]` Normalize failures into `auth`, `authorization`, `protocol`, `schema`, `tool-domain`, `dependency`, `timeout`, `cancelled`, `policy-denied`, and `unknown`. Preserve the original JSON-RPC/HTTP code and domain error. A model-friendly string alone is insufficient for operations or retry policy.

### Testing layers

1. **Contract**: schema examples, unknown fields, output validation, URI normalization, error-plane distinction.
2. **Protocol conformance**: current and legacy eras, required metadata, headers, pagination, caching, MRTR, subscriptions, cancellation.
3. **Policy/security**: tenant isolation, scope step-up, token audience, path/command/SQL injection, SSRF, prompt injection, tool shadowing, definition change.
4. **Resilience**: timeout after commit, replica changes between MRTR rounds, server restart, SSE proxy close, duplicate request, cache loss.
5. **Load**: catalog size, concurrent calls, slow backend, large resources, stream count, per-tenant fairness.
6. **End-to-end model eval**: correct tool selection, valid arguments, refusal/approval, data-flow policy, verified task outcome and cost.

The official Inspector is useful for protocol exploration and CI smoke tests, but a development UI is not a security scanner or a substitute for workload tests. Keep it patched and do not connect privileged local launchers to untrusted servers.

## 6. Enterprise System Design Scenarios

### Scenario A: local coding assistant

**Need:** read a checked-out repository and run a bounded formatter.

**Design:** one stdio server installed by digest, launched by the IDE host inside a sandbox. Expose project files as resources under a canonical `file://` subtree; expose `format.check` as read-only and `format.apply` as a separate write tool. Mount only the workspace, strip inherited environment variables, disable network, cap process/CPU/output, and require confirmation showing files changed. Logs go to stderr.

**Why:** stdio avoids a listening network service and matches one host/one local process. It does not remove package or local-code risk. Do not expose a generic shell tool when two bounded operations satisfy the requirement.

### Scenario B: enterprise knowledge server

**Need:** many hosts retrieve policy documents under user entitlements.

**Design:** stateless Streamable HTTP behind an API gateway. OAuth tokens are audience-bound to the MCP resource. `resources/list` returns only authorized descriptors with `cacheScope: private`; `resources/read` rechecks document ACL and returns bounded excerpts plus provenance. A `knowledge.search` tool returns resource links rather than embedding entire documents. Replicas share no MCP session; catalogs and reads use conservative TTLs and subscription events invalidate host caches.

**SLO/evaluation:** measure authorized retrieval recall, forbidden-document leakage (target zero), list/read p95, cache hit rate, bytes/tokens per verified answer, stale-read window, and tenant fairness. A public cache scope is wrong even if two users currently see the same list.

### Scenario C: transactional finance server

**Need:** read invoices and issue refunds.

**Design:** separate read and write tools. `refund.preview` computes effect without mutation; `refund.execute` requires step-up scope, trusted-host approval of invoice/amount/destination, and an idempotency key. The domain service stores the idempotent result and exposes `refund.status`. An ambiguous timeout never triggers an unqualified retry. Backend ledger ID, approval, policy version, and tool definition digest enter the audit record. The server uses `input_required` only for additional structured input; authorization is always deterministic server-side.

**Why:** tool descriptions and `destructiveHint` cannot enforce financial policy. MRTR makes a stateless confirmation exchange possible, but only domain idempotency and ledger state resolve commit ambiguity.

### Scenario D: multi-tenant MCP gateway

**Need:** central governance across many internal servers and model hosts.

**Design:** gateway authenticates principal/workload, selects an allowlisted backend, rewrites colliding tool names into stable host namespaces, filters catalogs by policy, enforces per-tool quotas and output limits, and propagates trace context. It verifies `MCP-Protocol-Version`, `Mcp-Method`, `Mcp-Name`, selected `Mcp-Param-*`, and the JSON body before routing. Private cache keys include tenant/principal, endpoint, revision, and catalog digest. Backend credentials are exchanged or delegated narrowly, never a shared super-token.

**Failure containment:** circuit-break per server/tool, bulkhead high-cost tools, limit concurrent SSE subscriptions, redact trace/log fields, and deny cross-server data flows prohibited by classification. A gateway increases consistency but becomes a confused-deputy and blast-radius concentration point; policy tests and tenant-isolation tests are release gates.

### Scenario E: agent delegation plus capabilities

**Need:** a procurement agent delegates a vendor review to a remote compliance agent, which queries internal controls.

**Design:** use A2A for remote-agent discovery, task lifecycle, messages, and artifacts. Inside the compliance agent, use MCP clients for read-only policy resources and evidence tools. Keep A2A task credentials, MCP resource tokens, and backend credentials audience-separated. Return evidence references and task artifacts, not another agent disguised as a single opaque MCP tool unless only one bounded synchronous operation is intended.

### Decision matrix

| Question | Prefer |
|---|---|
| local process owned by one host? | stdio with sandbox and pinned artifact |
| shared remote service or horizontal scale? | modern Streamable HTTP with OAuth/gateway |
| passive context selected/filtered by host? | resource |
| bounded computation or side effect selected by model? | tool |
| durable remote autonomy, task state, artifacts? | A2A/workflow API, possibly MCP inside |
| one private app and one stable backend, no portable discovery needed? | direct typed API may be simpler |
| confirmation or missing input during call? | MRTR plus trusted host UI; deterministic policy remains server-side |
| long work with recovery? | durable workflow/queue and negotiated Tasks or start/status/cancel tools |

### Production readiness checklist

- Pin the MCP revision required by the product or document dual-era behavior and fallback.
- Run SDK conformance plus host/server interoperability tests on both transports used.
- Inventory every server artifact/endpoint, owner, tool/resource, scopes, data class, network/filesystem grant, and definition digest.
- Filter catalogs per authenticated principal and task; namespace collisions deterministically.
- Enforce strict input/output/resource limits and authorization inside every handler.
- Add domain idempotency for writes; distinguish tool errors from protocol errors.
- Partition private caches and refresh authoritative state after subscription reconnect.
- Implement OAuth metadata, PKCE, issuer/audience/resource validation, token separation, minimal scopes, and step-up where applicable.
- Sandbox local servers and local proxies; patch Inspector/bridges; never inherit broad secrets.
- Treat all descriptions/resources/results as untrusted; test indirect injection and cross-server exfiltration.
- Trace negotiated era, catalog digest, policy/approval, backend ID, latency, tokens, retries, and redacted errors.
- Load-test catalogs, calls, large resources, SSE streams, backend failure, replica changes, and ambiguous commits.
- Provide per-server/tool kill switches, token revocation, catalog quarantine, and rollback.

> ⚠️ Limited public data available for this dimension. Public standards describe interoperability mechanisms, and several papers/advisories demonstrate security failure classes, but there is no authoritative cross-vendor dataset for MCP production availability, incident frequency, latency, cost, tool-selection accuracy, or the prevalence of modern `2026-07-28` adoption. Enterprise architecture thresholds therefore require internal threat modeling, conformance, red-team evaluation, and load testing.

## Interview-focused synthesis

1. **What does MCP standardize?** A JSON-RPC capability protocol through which hosts/clients discover and invoke server tools, read resources, obtain prompts, and receive subscribed notifications across standard transports.
2. **Tool versus resource?** Tools are model-selectable operations; resources are host/application-selected context identified by URI. Neither label itself enforces read/write safety.
3. **What changed in 2026-07-28?** `server/discover` and per-request metadata replace initialization; protocol sessions disappear; MRTR replaces server-initiated requests; subscriptions replace unsolicited change delivery; list/read caching and HTTP routing headers become explicit.
4. **Why can modern servers scale more simply?** Any self-describing request can hit any replica. Application state still needs an explicit handle or durable backend.
5. **Does MCP provide exactly-once tools?** No. JSON-RPC IDs correlate responses, not business effects. Writes need domain idempotency and status reconciliation.
6. **How is remote authorization done?** OAuth-based protected-resource discovery, PKCE, issuer validation, resource/audience-bound tokens, minimal/step-up scopes, and per-request server policy. stdio usually receives narrowly scoped credentials from its environment.
7. **Main security insight?** A server is executable/integrated code plus untrusted semantic content. Sandbox and authenticate conventionally, then add model-specific defenses for poisoned metadata/results and cross-tool data flow.
8. **MCP versus A2A?** MCP equips an agent with capabilities; A2A lets independent agents exchange tasks/messages/artifacts. They compose at different layers.
9. **How should change notifications be used?** As cache invalidation hints. Reconnect and reread authoritative state because modern HTTP subscriptions are not resumable with `Last-Event-ID`.
10. **What should be observed?** Revision/era, server/artifact/catalog identity, authenticated principal, policy and approval, method/name, validated input hash, backend operation/idempotency, result class, latency, tokens, cache, retry, MRTR, and cancellation.

## Sources

1. [MCP maintainers - The 2026-07-28 Specification](https://blog.modelcontextprotocol.io/posts/2026-07-28/)
2. [JSON-RPC Working Group - JSON-RPC 2.0 Specification](https://www.jsonrpc.org/specification)
3. [MCP - Understanding MCP Servers](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/docs/2026-07-28/learn/server-concepts.mdx)
4. [MCP 2026-07-28 - Base Protocol Overview](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/basic/index.mdx)
5. [MCP 2026-07-28 - Discovery](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/server/discover.mdx)
6. [MCP 2026-07-28 - Tools](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/server/tools.mdx)
7. [MCP 2026-07-28 - Resources](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/server/resources.mdx)
8. [MCP 2026-07-28 - Transport Overview](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/basic/transports/index.mdx)
9. [MCP 2026-07-28 - stdio Transport](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/basic/transports/stdio.mdx)
10. [MCP 2026-07-28 - Streamable HTTP](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/basic/transports/streamable-http.mdx)
11. [MCP 2026-07-28 - Multi Round-Trip Requests](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/basic/patterns/mrtr.mdx)
12. [MCP 2026-07-28 - Subscriptions](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/basic/patterns/subscriptions.mdx)
13. [MCP 2026-07-28 - Authorization](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/basic/authorization/index.mdx)
14. [MCP 2026-07-28 - Authorization Security Considerations](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/basic/authorization/security-considerations.mdx)
15. [MCP - Security Best Practices](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/docs/2026-07-28/tutorials/security/security_best_practices.mdx)
16. [MCP 2026-07-28 - Changelog](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/changelog.mdx)
17. [MCP 2026-07-28 - Canonical Schema](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/schema/2026-07-28/schema.json)
18. [Official MCP TypeScript SDK v2](https://ts.sdk.modelcontextprotocol.io/v2/)
19. [MCP TypeScript SDK - Protocol Versions](https://ts.sdk.modelcontextprotocol.io/v2/protocol-versions)
20. [MCP TypeScript SDK - Client and Response Cache API](https://ts.sdk.modelcontextprotocol.io/v2/api/%40modelcontextprotocol/client/client/client.html)
21. [Official MCP Python SDK v2 - What's New](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/whats-new.md)
22. [MCP - SDK Tiering System](https://modelcontextprotocol.io/community/sdk-tiers)
23. [MCP Inspector - CLI Client](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/docs/2026-07-28/tools/inspector/cli.mdx)
24. [MCP - Official Registry Architecture and Trust](https://modelcontextprotocol.io/registry/about)
25. [MCP Registry - Moderation Policy](https://modelcontextprotocol.io/registry/moderation-policy)
26. [MCP Registry - Server Versioning](https://modelcontextprotocol.io/registry/versioning)
27. [OWASP - MCP Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/MCP_Security_Cheat_Sheet.html)
28. [GitHub Advisory - MCP Inspector XSS to Command Execution, CVE-2025-58444](https://github.com/advisories/GHSA-g9hg-qhmf-q45m)
29. [GitHub Advisory - mcp-remote OS Command Injection, CVE-2025-6514](https://github.com/advisories/GHSA-6xpm-ggf7-wc3p)
30. [Wang et al. - MCPTox](https://arxiv.org/abs/2508.14925)
31. [Zhang et al. - MCP Security Bench](https://arxiv.org/abs/2510.15994)
32. [Model Context Protocol at First Glance: Security and Maintainability of MCP Servers](https://arxiv.org/abs/2506.13538)
33. [A2A Protocol - MCP and A2A](https://a2a-protocol.org/latest/)
34. [OpenAI Responses API - MCP Tools](https://developers.openai.com/api/reference/cli/resources/responses/methods/create)
35. [RFC 8707 - Resource Indicators for OAuth 2.0](https://www.rfc-editor.org/rfc/rfc8707.html)
36. [RFC 9728 - OAuth 2.0 Protected Resource Metadata](https://www.rfc-editor.org/rfc/rfc9728.html)
37. [RFC 9207 - OAuth 2.0 Authorization Server Issuer Identification](https://www.rfc-editor.org/rfc/rfc9207.html)
38. [W3C - Trace Context](https://www.w3.org/TR/trace-context/)
39. [JSON Schema - Draft 2020-12](https://json-schema.org/draft/2020-12)
40. [MCP Extension - OAuth Client Credentials](https://modelcontextprotocol.io/extensions/auth/oauth-client-credentials)
