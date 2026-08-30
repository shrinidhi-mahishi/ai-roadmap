# Research: MCP & Interoperability — Tools, Resources, MCP Servers/Clients

**Date researched**: 2026-08-21
**Sources consulted**: 26

## 1. System Topology & Mechanics

**Core architecture.** MCP is a JSON-RPC 2.0-based protocol connecting three roles: **Hosts** (LLM applications that initiate connections, e.g. Claude Desktop, Cursor), **Clients** (connectors embedded in the host, one per server connection), and **Servers** (processes exposing context/capabilities) [1][2]. This host/client/server split lets one host manage many isolated client↔server sessions, each independently capability-negotiated.

**The three primitives** (control who decides invocation) [3][4]:
| Primitive | Direction | Discovery | Invocation | Controller |
|---|---|---|---|---|
| Tools | Server→Client | `tools/list` | `tools/call` | Model decides |
| Resources | Server→Client | `resources/list`, `resources/templates/list` | `resources/read` | Application decides |
| Prompts | Server→Client | `prompts/list` | `prompts/get` | User decides |

Reverse-direction primitives exist too: **Sampling** (`sampling/createMessage`, server asks client's LLM for a completion) and **Elicitation** (`elicitation/request`, server asks client for structured user input) [3]. Tools use JSON Schema (2020-12) for input and optional structured output validation [1].

**Capability negotiation.** Historically (spec ≤2025-11-25), sessions began with an `initialize`/`initialized` handshake exchanging supported capabilities before any request could proceed [5][6]. The **2026-07-28 spec** (current as of this research date) removed the handshake and session IDs entirely: every request is now self-describing and independent, carrying its own protocol-version metadata (`io.modelcontextprotocol/protocolVersion` in `_meta`) [6][7]. Clients that want capability info up front call the new mandatory `server/discover` RPC, but this is optional — any request can be sent directly and a version/capability mismatch surfaces as an error [7].

**Transport mechanics.** The spec (2026-07-28) defines exactly two standard transports [8][9]:
- **stdio**: client launches server as a local subprocess; newline-delimited JSON-RPC over stdin/stdout. Zero network overhead, single-client-per-process, no built-in auth (relies on OS-level process isolation) [8][10].
- **Streamable HTTP**: single endpoint (e.g. `/mcp`) accepting POST (and, pre-2026-07-28, GET); replies are either a plain JSON body or an upgraded request-scoped SSE stream. Supports horizontal scaling, OAuth, and multi-tenancy [8][9][10].
- **HTTP+SSE** (two separate endpoints — GET for the SSE stream, POST for messages) is the original 2024-11-05 transport, **deprecated since spec 2025-03-26**; new builds must not use it [8][9].

A binding is purely a **framing/delivery** contract — protocol semantics (message patterns, cancellation via `notifications/cancelled` or stream closure) are transport-agnostic [11].

**Control/data plane separation (gateway pattern).** In enterprise deployments, an **MCP gateway** sits between clients and many backend servers, forming a **control plane** (registry, identity/auth, policy engine, discovery of unapproved "shadow" servers, audit infrastructure) distinct from the **data plane** (the actual tool-call execution path) [12][13]. This is explicitly analogized to Kubernetes: the control plane defines/enforces desired state (who can call what), the data plane executes it [13]. The 2026-07-28 spec formalizes this further: `Mcp-Method` and `Mcp-Name` HTTP headers (SEP-2243) let gateways route/authorize/rate-limit purely on headers without parsing JSON-RPC bodies [7].

**Notable spec extensions (2026-07-28):** Tasks (async long-running ops with polling/durable handles, promoted out of experimental status), Skills-over-MCP (structured agent workflow instructions), and MCP Apps (inline interactive UI) [1].

> ⚠️ The 2026-07-28 stateless redesign is very recent; some ecosystem tooling, gateways, and third-party servers may still assume the older `initialize`/`Mcp-Session-Id` handshake model, creating a live compatibility surface (see §5 schema drift).

## 2. Token Economics & NFR Metrics

**Tool schema overhead vs. native function calling.** MCP tool definitions are structurally heavier than inlined native function-calling schemas because each includes name, description, full JSON Schema, field descriptions, and enums, transmitted on every turn (no persistent server-side registration) — measured at **550–1,400 tokens per tool** [14]. In a controlled Scalekit benchmark (Claude Sonnet 4, 75 head-to-head comparisons of MCP vs. CLI-equivalent operations), MCP cost **4×–32× more tokens** than CLI for identical tasks; the simplest task (checking a repo's language) consumed 1,365 tokens via CLI vs. **44,026 tokens via MCP** (43 tool definitions injected up front, of which 1–2 were used) [14].

**Real-world multi-server measurements** [15]:
| Configuration | Tool-schema tokens | % of 200K window |
|---|---|---|
| GitHub MCP alone (35 tools) | ~26,000 | 13% |
| Slack MCP alone (11 tools) | ~21,000 | 10.5% |
| GitHub + Playwright + IDE (3 servers) | ~143,000 | 72% |
| 5-server modest config | ~55,000 | 27.5% |
| 10-server power-user config | ~75,000 | 37.5% |
| Cloudflare, full native MCP (pre-Code Mode) | ~1,170,000 | exceeds any window |

This is termed the **"Tools Tax"/"MCP Tax"**, independently corroborated in the academic literature at **~10k–60k tokens per turn** in typical multi-server deployments [16]. Context utilization above a published **~70% fracture point** is associated with measurable reasoning degradation [16].

**Mitigations and their measured effect:**
- **Anthropic Tool Search** (subagent-gated tool loading, GA Feb 2026): preserved **85% of context** vs. conventional eager loading [15].
- **Cloudflare Code Mode** (expose a sandboxed code-execution surface instead of per-tool schemas): compressed **1.17M tokens → ~1,000 tokens** (~99.9% reduction) for native MCP definitions [15]; in Cloudflare's own internal deployment, 52 tools across 4 servers (9,400 tokens of schema) collapsed to 2 portal tools (~600 tokens), a **94% reduction**, with cost staying **flat** as more servers are added behind the portal [17].
- **Academic "Tool Attention" middleware** (Intent–Schema Overlap gating + lazy schema loading, arXiv 2604.21816): reduced simulated per-turn tool tokens by **95.0% (47.3k→2.4k)** and raised effective context utilization from 24%→91% in a 120-tool/6-server benchmark [16]. End-to-end task-success/latency/cost projections in this paper are explicitly marked as *projected, not measured on live agents* [16] `[inferred where noted by source]`.
- **Block's "layered tool pattern"**: collapsed Square's 200+ REST endpoints into **3 conceptual tools** (discover/plan/execute layers) rather than one-tool-per-endpoint, citing that 1:1 endpoint-to-tool mapping "doesn't scale" and caused frequent errors plus context blowup [18].
- Practical guidance converges on a ceiling of **~30–40 always-loaded tools**, deferring the rest via search/lazy-loading [15].

**Latency: stdio vs. HTTP/SSE.** stdio has near-zero network overhead (local pipe) but no horizontal scalability; Streamable HTTP benchmarks show **~10ms latency under load** for simple echo tools, but real servers with backend I/O will be materially slower [10]. Throughput testing (ToolHive on Kubernetes) found Streamable HTTP with **shared sessions sustaining 290–300 req/s** vs. only **30–36 req/s with unique sessions per request** — a **~10× difference** purely from session-pooling strategy [19]. Legacy stdio-over-container-attachment architectures scaled poorly: one test recorded only 2 of 50 requests succeeding under concurrency due to per-connection container resource costs [19].

**Caching of tool discovery.** As of 2026-07-28 (SEP-2549), `tools/list`, `prompts/list`, `resources/list`, and `resources/read` responses carry `ttlMs` and `cacheScope` fields, letting clients cache tool catalogs deterministically and keep upstream prompt caches stable across reconnects [7] — directly mitigating the re-fetch cost of the stateless redesign.

**Throughput under load.** Purpose-built MCP load-testing tools (mcpbench, mcp-load-lab, mcpdrill) report **p50/p95/p99 latency per tool** as the standard NFR unit; one reference run showed p99 88ms at ~98 RPS for a filesystem-style server, with CI regression gates flagging >20% p95 degradation as a build failure [20]. A Locust-based Azure Load Testing run against 4 production hosted MCP servers found **sub-second p90** for cached/simple servers (Microsoft Learn) vs. **>1s on every call** for a synchronous embedding-backed server (Context7), with search tools on GitHub showing **2.8× average-to-p90 ratio**, symptomatic of upstream rate limiting bleeding through the tool layer [21].

## 3. Distributed Resilience & State

**Statelessness is now the default architectural stance.** Google and Cloudflare both published detailed accounts of hitting a "hard wall" scaling MCP on cloud-native infra because the pre-2026 protocol pinned clients to specific pods via `Mcp-Session-Id`, forcing sticky-session load balancing, complex drain-on-deploy logic, and broken sessions on autoscale/restart [22][23]. The **2026-07-28 spec removes the handshake and session ID entirely** (SEP-2575, SEP-2567), making the protocol core stateless: pod restarts, rollouts, and autoscaling become invisible to the client, and requests can hit any healthy replica behind an ordinary round-robin load balancer [22][23].

**Trade-off: statelessness vs. resumability.** Stateless mode survives process restarts trivially (nothing to lose) but gives up mid-stream resume — a dropped connection must be **retried from scratch**, which is only safe if tool calls are idempotent [24]. If an application genuinely needs SSE resumability (`Last-Event-ID` replay), it must externalize the event log to a shared, durable store (e.g., Redis-backed `EventStore` keyed by session+stream with an eviction cap) since SDK default `EventStore` implementations live in single-process memory and return 404 on restart or fail to share across replicas [24].

**Durable execution.** For multi-step tool logic that must survive crashes, teams offload to external workflow engines rather than reimplementing retry/resume inside the MCP server itself:
- **Dapr `MCPServer` resource**: auto-registers a durable workflow orchestration per discovered tool; a tool call becomes "start a workflow." Dapr Scheduler re-delivers pending activities to a new instance on daprd restart, and Dapr keeps one warm session per backend MCP server with automatic reconnect-once-on-`ErrConnectionClosed` [25].
- **Temporal**: wraps each MCP tool as a thin invoker of a Temporal Workflow; all business logic and external API calls execute as **Activities** with configurable automatic retry policies, guaranteeing completion despite process restarts or network failures [26].

**Circuit breakers and layered resilience.** Convergent industry guidance recommends a fixed "resilience onion," executed in this order around every external call an MCP tool makes [27][28][29]:
1. **Rate limiter** (admission control)
2. **Bulkhead** (isolate resource pools per dependency to prevent cascading exhaustion)
3. **Circuit breaker** (Closed→Open→Half-Open state machine; typical threshold ~5 consecutive failures, ~60s cooldown before a Half-Open probe) [27][28]
4. **Retry with exponential backoff + jitter** (avoids thundering herd) [27][28]
5. **Timeout** (bounds latency per attempt, not the whole retry budget) [29]
6. **Fallback** (cached/partial/default response) [28][29]

Key MCP-specific guidance: implement circuit breakers **per external dependency, not per tool**, since multiple tools often share one backend API [27][28]; surface breaker state in the **tool-error message text** itself (e.g., "circuit breaker is open — will retry automatically at 14:32:00") so the calling LLM can reason about whether to retry, fall back, or alert the user rather than blindly re-invoking [28]; and always route logs to **stderr**, never stdout, to avoid corrupting the stdio JSON-RPC stream [28].

> ⚠️ No vendor-neutral, production-scale benchmark was found quantifying MTBF/error-budget improvements from these resilience patterns specifically for MCP (as opposed to microservices generally) — figures above are pattern prescriptions, not measured outcome deltas `[inferred]`.

## 4. Enterprise Security & Governance

**Zero Trust MCP model.** Applying NIST SP 800-207 to MCP: no user, client, server, token, tool, workload, package, or network location receives automatic trust; every request must be authenticated, authorized against a *specific* resource server, scoped to least privilege, evaluated against live context, executed in an isolated boundary, and continuously monitored [30][31]. Concretely this decomposes into:
- **On-Behalf-Of (OBO) token flows** to prevent **Confused Deputy** attacks — an agent must exercise only the calling user's permissions, not its own standing service-account power [31][32].
- **Workload identity** (SPIFFE/SPIRE, AWS IRSA, Azure Managed Identity) issuing cryptographic identity to ephemeral MCP server processes/containers, combined with user OBO identity for defense-in-depth (a valid call requires *both* correct user permission *and* a verified workload origin) [31][33].
- **OAuth 2.0 Token Exchange (RFC 8693)** to narrow token scope/audience at each hop in a multi-server chain rather than passing one all-powerful user token end-to-end [32].
- **Tool-level and argument-level authorization**, evaluated on every `tools/call`, not just at server-connect time — deterministic policy enforcement (OPA/Cedar-style PDP) that does not depend on the model "following instructions" [13][30].

**OAuth for MCP (2025–2026 spec evolution).** The MCP server acts strictly as an **OAuth 2.1 resource server** — it validates tokens but never issues them; a separate authorization server (co-hosted or external) mints tokens [34][35]. Mandatory mechanisms as of spec 2025-06-18/2025-11-25 [34][36][37]:
- **RFC 9728** (OAuth 2.0 Protected Resource Metadata): servers **MUST** serve `/.well-known/oauth-protected-resource`; on a `401`, the `WWW-Authenticate` header **MUST** point to this metadata plus a required `scope` [36][37].
- **RFC 8414** (Authorization Server Metadata): the authorization server **MUST** publish its own discovery document; clients use it to locate authorization/token endpoints [34].
- **RFC 8707** (Resource Indicators): clients **MUST** include a `resource` parameter (the MCP server's canonical URI) in both authorization and token requests, and servers **MUST** validate that the returned token's `aud` claim matches — this is the primary defense against **token replay across MCP servers** [34][36][37].
- **PKCE with S256 is mandatory** (the `plain` challenge method is forbidden); a client must refuse to proceed if the authorization server doesn't advertise `code_challenge_methods_supported` [35][37].
- As of the **2025-11-25 revision**, any internet-reachable MCP server **MUST** implement OAuth 2.1 with PKCE — static "paste an API key" auth is explicitly non-compliant for public endpoints (stdio/localhost servers are exempt) [37].

**RBAC / governance at the gateway.** Because MCP has no built-in enterprise RBAC, organizations centralize it at a **gateway/control-plane** layer that fronts every downstream server [12][38][39]:
- Auth (OAuth 2.1/SSO termination, e.g. Entra ID/Okta), RBAC (per-user/per-role allowed server+tool+scope combinations), audit (atomic tool-call-level structured logs: timestamp, user identity, agent identity, tool name, parameters, response summary, policy decision), rate limiting, and policy (reject poisoned descriptions, enforce least-privilege, redact PII) [39][12].
- A documented industry deployment pattern uses a **two-axis auth model** — persona (interactive user vs. automated non-user) × credential type (no-auth, static/dynamic API key, PKCE-authcode, client-credentials, platform app-context) — all served through one MCP endpoint, plus three enterprise SSO grant types (Auth Code+PKCE, Device Code, ROPC) and three token-provisioning models (Bring-Your-Own-Token, Generate-Your-Own-Token, delegated OAuth via RFC 8693) [12].
- Recommended rollout: deploy the gateway in **logging-only mode** for several weeks to baseline traffic before enabling active enforcement, rate limiting, and tool-hash pinning [39][38].
- Vendor/pattern landscape: Kong AI MCP Proxy, Azure API Management MCP (Entra ID, policy expressions), Cloudflare AI Gateway/MCP portals, MintMCP (SCIM-driven RBAC + Virtual MCP Bundles), Operant MCP Gateway (SPIFFE/SPIRE) [38][40].

**PII redaction at the MCP boundary.** Standard endpoint DLP tools cannot see MCP tool-call payloads, so redaction must move to an **inline gateway/proxy layer** [41][42][43]:
- **Classification**: regex for structured PII (SSNs, keys, card numbers) plus NLP models (e.g., Microsoft Presidio) for unstructured PII (names, addresses) [42][44].
- **Redaction before context injection**: mask/tokenize/hash sensitive fields in the tool *response* before it ever reaches the model's context window — not after [41][44].
- **Zero Data Retention architecture**: the gateway must act as a pure pass-through proxy, resolving OAuth tokens and applying redaction transforms **in memory only**; if the platform persists raw tool-response bodies to disk, it legally becomes a **sub-processor**, expanding SOC 2/HIPAA/GDPR audit scope [43].
- Regulatory mapping: RBAC → EU AI Act, HIPAA; audit trail → SOC 2, GDPR; token vault (short-lived, scope-limited credentials, never exposed to the agent) → GDPR, SOC 2 [39].

**Sandbox isolation for MCP servers.** Three tiers, trading startup latency for isolation strength [45][46][47]:
| Approach | Startup | Isolation | Example use |
|---|---|---|---|
| OS-level (bubblewrap/seatbelt) | <10ms | Process-level | Anthropic Claude Code CLI (local) [46] |
| gVisor (userspace kernel intercepting syscalls) | ~500ms | Container+ | Anthropic Claude web, multi-tenant cloud [46][47] |
| Firecracker microVM | ~125ms | Hardware/VM-level (dedicated kernel) | Vercel Sandbox, "paranoid" managed platforms [45][46] |

A documented gVisor test running Anthropic's own reference filesystem MCP server under 60+ adversarial inputs (`--network none`, `--cap-drop ALL`, `--read-only`) blocked all network calls, sensitive-path writes, process spawning, and `/proc`/`/etc/shadow` access attempts — demonstrating gVisor's syscall-level containment even against a compromised/malicious server binary [47].

## 5. Production Failure Modes

**Tool Poisoning Attacks (TPA).** Disclosed by Invariant Labs (April 2025): malicious instructions embedded in tool **metadata** (descriptions, parameter docs) — invisible to the user's UI but fully visible to the LLM — can manipulate the agent into exfiltrating data or taking unauthorized actions **without the poisoned tool ever needing to be executed** [48]. When multiple MCP servers share a client context, a single malicious server can poison descriptions to hijack authentication credentials meant for a *different*, trusted server, or override that server's instructions entirely [48].

Quantified severity from the **MCPTox benchmark** (45 live real-world MCP servers, 353 authentic tools, 1,312 adversarial test cases, 20 LLM agents) [49][50]:
- Average attack success rate across all 20 models: **36.5%**.
- Highest: **72.8%** (OpenAI o1-mini).
- Counterintuitive finding: **more capable models are often more susceptible**, because the attack exploits superior instruction-following [49][50].
- Refusal rates were uniformly low — the best (Claude 3.7 Sonnet) refused **less than 3%** of attacks, and still complied in ~34% of poisoned-tool test cases [49][50].
- A companion STRIDE/DREAD threat-modeling study across 5 MCP components (Host+Client, LLM, Server, External Data Stores, Authorization Server) identified **57 distinct threats**, with tool poisoning rated the most prevalent and impactful **client-side** vulnerability across all 7 major MCP clients tested [51].
- The **MCPLib** attack taxonomy catalogs **31 distinct attack methods** across 4 classes: direct tool injection, indirect tool injection, malicious user attacks, and LLM-inherent attacks [52].

**Named real-world incidents/CVEs:**
| Date | Incident/CVE | Severity | Description |
|---|---|---|---|
| Apr 2025 | WhatsApp MCP tool poisoning | n/a | Demonstrated attack (AuthZed) [53] |
| Jun 2025 | CVE-2025-49596 (Anthropic MCP Inspector) | CVSS 9.4 | Unauthenticated RCE via browser/DNS rebinding + 0.0.0.0 binding [53][54] |
| Jul 2025 | CVE-2025-6514 (`mcp-remote` npm, 437K+ downloads) | CVSS 9.6 | OS command injection via malicious OAuth `authorization_endpoint`; first documented full RCE on client OS from an untrusted remote MCP server [53][54][55] |
| Jul 2025 | CVE-2025-54136 "MCPoison" (Cursor) | CVSS 7.2–8.8 | Trust bound to MCP server *name* not contents; editing an already-approved shared `.cursor/mcp.json` silently swapped in a malicious command, enabling team-wide compromise from one committed file [53][54][47] |
| Aug 2025 | CVE-2025-54135 "CurXecute" (Cursor) | CVSS 9.8 | Workspace-file write via prompt injection → RCE through MCP auto-start [53][54] |
| Aug 2025 | CVE-2025-53109/53110 "EscapeRoute" (Anthropic Filesystem MCP) | CVSS 7.3/8.4 | Symlink and path-prefix containment bypass [53][54] |
| Sep 2025 | Postmark MCP (npm) supply-chain trojan | n/a | BCC-based data exfiltration hidden in package update [53] |
| Sep 2025 | Flowise CustomMCP node | CVSS 10.0 | STDIO transport → RCE [53] |
| Sep 2025 | npm worm "Shai-Hulud" | n/a | Harvested npm/GitHub/AWS/GCP tokens from infected maintainer machines, republished ~500 packages with itself as `postinstall`; a Nov 2025 "Shai-Hulud 2.0" wave hit ~796 more [56] |
| Jan 2026 | CVE-2025-68143/68144/68145 (Anthropic `mcp-server-git`) | CVSS up to 9.1 | 3 chained flaws incl. path traversal, argument injection [53][54] |
| Jan 2026 | CVE-2026-0755 (gemini-mcp-tool) | CVSS 9.8 | Command injection via `execAsync` [53] |
| Mar 2026 | CVE-2026-33032 "MCPwn" (nginx-ui) | CVSS 9.8 | Auth bypass → RCE, actively exploited [53][54] |
| Jan–Apr 2026 | OX Security advisory: systemic **STDIO command injection** across official SDKs | Critical (by-design) | 10 CVEs incl. CVE-2025-65720, CVE-2026-30615/30617/30618/30623/30624/30625/33224/26015/40933, spanning Python/TypeScript/Java/Rust SDK consumers (GPT Researcher, LiteLLM, Agent Zero, Windsurf, LangChain-Chatchat, etc.); est. **200,000 vulnerable servers, 150M+ combined downloads**. Anthropic has explicitly declined to patch some of these as "by design" [54][57] |
| May 2026 | Microsoft-disclosed dependency-confusion campaign | n/a | 33 malicious npm packages impersonating 9 internal corporate scopes, silent reconnaissance `postinstall` payload with a togglable "full exploit" flag [58] |

**Aggregate CVE statistics:** As of August 2026, the community-maintained `mcp-cve-project` indexes **313 CVEs** touching the MCP ecosystem (servers, SDKs, gateways, clients) [59]. Independent scans found **30–82% of public MCP servers carry exploitable flaws**, and only **8.5% use OAuth** [54]. In a single 60-day window in early 2026, **30+ CVEs** were filed, of which **43% were command-injection patterns**; broader Jan–Apr 2026 breakdown: shell/exec injection 43%, tooling infrastructure flaws 20%, auth bypass 13%, path traversal + other (SSRF, cross-tenant, supply chain) ~24% [53][57].

**Supply-chain risk mechanics.** The dominant install pattern — `npx -y some-mcp-server` or `uvx some-mcp-server` — resolves the full **transitive dependency tree** from a public registry and executes it with the full privileges of the host (filesystem, env vars, network), *before* any MCP handshake even begins; `postinstall`/`preinstall` lifecycle scripts run at install time, meaning runtime MCP-layer policy enforcement (allowlists, gateway auth) **cannot intercept the payload** [55][60]. Unpinned `@latest` installs are a bet against a compromise window measured in **hours** (the documented gap between a malicious npm release going live and registry takedown) [60]. Container isolation with restricted egress (e.g., Stacklok's ToolHive) was independently confirmed effective against the widely-felt Sept 2025 npm attack that hit foundational JS/TS packages (2B+ weekly downloads) that were indirect dependencies of the official MCP TypeScript SDK [55].

**Schema drift between client/server versions.** Two distinct failure classes: (1) **protocol-version drift** — client and server disagree on handshake era (legacy `initialize`-based ≤2025-11-25 vs. modern stateless 2026-07-28+); servers should implement graceful negotiation (`UnsupportedProtocolVersionError` listing supported versions) rather than hard-rejecting, and clients should probe with `server/discover` (stdio) or send-and-inspect-400-body (Streamable HTTP) to detect the server's era and fall back accordingly [61][62][63]. (2) **Implementation-level schema drift** — the published `tools/list` JSON Schema diverges from what the actual handler code expects/reads (e.g., schema marks a field optional but the handler dereferences it unconditionally, or field name casing mismatches like `userId` vs `user_id`); this is invisible to protocol-level validation and only caught by integration tests that exercise the real handler against the declared schema [64].

## 6. Enterprise System Design Scenarios

**Block (Square/Cash App), production scale case study** [65][66][67][68]:
- Rewrote internal agent "Goose" as a native MCP client; scaled from an engineering tool to **12,000 employees across 15 job functions in 8 weeks**.
- Built and pre-approved **100+ internal MCP servers** (from ~60 in week one) covering Slack, Google Calendar, data warehouse, and internal systems — bundled by default rather than requiring self-service discovery.
- Replaced manual API-key management with **OAuth + identity-provider SSO** integration.
- Solved the "1 tool per REST endpoint doesn't scale" problem for Square's **200+ endpoint / 30+ API platform** using a **3-tool layered pattern** (discovery/planning/execution layers) instead of ~200+ individual tool definitions — directly addressing both context-window bloat and tool-selection error rates.
- Added dynamic context management (auto enable/disable servers based on query) and a context summarizer for long conversations.
- Reported outcome: 75% of engineers saving 8–10 hours/week within the first month; company-wide reports of 50–75% time savings on common tasks.

**Cloudflare enterprise reference architecture** [17]:
- MCP server portals front multiple internal MCP servers with **default-deny write controls**, audit logging, auto-generated CI/CD, and centralized secrets management — deploying via a governance-approved template rather than ad hoc.
- **Code Mode** applied at the portal level: 4 internal servers / 52 tools / ~9,400 tokens of schema → 2 portal tools / ~600 tokens (**94% reduction**), with the key property that *token cost stays flat as more servers are added behind the portal* — directly addressing the linear-growth token economics problem in §2.
- Uses stateless Workers for the compute layer and Durable Objects only where genuine coordinated state is required — mirroring the general "stateless by default, stateful by exception" principle from §3.
- Detects **Shadow MCP** usage (unsanctioned server connections) via Cloudflare Gateway network-layer visibility.

**Capacity-planning implications** (synthesized from §2/§3 data):
- Per-agent context budget must reserve headroom for tool schemas: a naive 3-server / 40-tool deployment can consume **>70% of a 200K-token window** before any user content is processed [15], so capacity planning for multi-tool agents must budget token cost **per connected server**, not just per conversation turn.
- Session-affinity strategy is now a throughput lever, not just a reliability concern: shared-session Streamable HTTP pools measured **~10× the throughput** of unique-session-per-request pools under identical load [19].
- Sandbox isolation choice is a latency/security trade dial: OS-level (<10ms) for trusted local CLI tools, gVisor (~500ms) for multi-tenant cloud MCP servers, Firecracker (~125ms but requires KVM/bare-metal) for the highest-assurance managed platforms [46].

**Trade-off matrix: MCP vs. native function calling vs. OpenAPI** (synthesized across sources) [69][70][71][72][73]:
| Dimension | Native Function Calling | OpenAPI-generated tools | MCP |
|---|---|---|---|
| Best fit | App-local, latency-sensitive, small stable toolset, single agent | Enterprise HTTP service estates needing governed contracts/SDKs/docs | Cross-runtime portability; shared tool infra across many agents/IDEs/products |
| Governance | App-owned, ad hoc | Mature (OAS security schemes, API gateways) | Emerging (gateways layer RBAC/audit on top — not native to protocol) |
| Latency | Shortest (single LLM turn, local exec) | +1 network hop, but gateway retries/caching | Adds a session/schema layer; stdio is fast, HTTP as fast as infra allows |
| Portability | Vendor-specific (tied to one model API) | Vendor-neutral, mature ecosystem | Vendor-neutral, purpose-built for multi-host/multi-agent reuse |
| Discovery | Static, developer-declared | Static spec, can auto-generate tools | Dynamic (`tools/list`), supports live catalog + `listChanged` notifications |
| Reliability track record | Most battle-tested (OpenAI: billions of calls/day) | Inherits REST API reliability | Newer; maturing rapidly but carries the CVE/attack-surface profile in §5 |
| Recommended pattern | Fast-path actions in latency-critical UI | System-of-record contract | Curated subset exposed via MCP for cross-host reuse |

Consensus recommendation across sources: **OpenAPI remains the source-of-truth contract**; MCP servers are generated/wrapped on top of it for agent connectivity; function calling is reserved for narrow, latency-critical, single-agent paths — these are complementary layers, not competing standards [70][72][73].

**A2A protocol — the complementary interoperability layer.** Google's Agent2Agent (A2A) protocol, now under Linux Foundation governance (>150 supporting orgs as of April 2026, including AWS, Cisco, Google, IBM Research, Microsoft, Salesforce, SAP, ServiceNow) [74][75], addresses **agent-to-agent** communication as distinct from MCP's **agent-to-tool** scope [75][76]. Architecture: a layered model — Layer 1 canonical Protocol-Buffer data model, Layer 3 concrete bindings (JSON-RPC over HTTP(S), gRPC, HTTP/REST) [74]. Key mechanics: **Agent Cards** for capability discovery, support for sync request/response, SSE streaming, and async push notifications for long-running tasks [76][77]. The emerging 2026 production pattern is a **two-layer agent stack**: A2A between specialist agents, MCP between each specialist and its tools — "the architecture that maps most cleanly onto how production multi-agent systems are actually being built" [75].

**MCP registry landscape.** The **Official MCP Registry** (Anthropic + GitHub + PulseMCP + Microsoft) provides namespace-verified metadata (reverse-DNS server names tied to verified GitHub accounts/domains) but explicitly does **not** perform code security scanning — that is delegated to underlying package registries (npm/PyPI/Docker Hub) and downstream marketplace aggregators (Glama, MCPMarket, MCP.so, Smithery, LobeHub) [78]. This division of responsibility is a direct contributor to the supply-chain risk profile in §5.

## Sources
- [1] https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/index.mdx — MCP 2026-07-28 spec overview, primitives, extensions
- [2] https://modelcontextprotocol.io/specification/2026-07-28 — Canonical MCP specification landing page
- [3] https://www.webfuse.com/mcp-cheat-sheet — MCP primitive quick-reference table (tools/resources/prompts/sampling/elicitation)
- [4] https://modelcontextprotocol.io/docs/learn/server-concepts — Server concepts, primitive control model (model/application/user)
- [5] https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization — Legacy handshake-based auth spec
- [6] https://blog.modelcontextprotocol.io/posts/2026-07-28/ — Official blog on 2026-07-28 spec changes (stateless core, MRTR, cache hints)
- [7] https://blog.modelcontextprotocol.io/posts/2026-07-28/ — Mcp-Method/Mcp-Name headers, ttlMs/cacheScope caching (SEP-2243, SEP-2549)
- [8] https://startdebugging.net/2026/07/mcp-stdio-vs-http-vs-sse-transport-which-to-choose/ — Transport decision guide, deprecation timeline
- [9] https://modelcontextprotocol.io/specification/2026-07-28/basic/transports — Official transport binding spec
- [10] https://www.truefoundry.com/blog/mcp-stdio-vs-streamable-http-enterprise — stdio vs Streamable HTTP architecture/latency/enterprise trade-offs
- [11] https://modelcontextprotocol.io/specification/2026-07-28/basic/transports — Transport-agnostic message pattern guarantees, cancellation semantics
- [12] https://arxiv.org/html/2608.10760 — Enterprise MCP gateway auth architecture (two-axis model, OBO, RFC 8693)
- [13] https://obot.ai/resources/learning-center/what-is-mcp-control-plane/ — MCP control plane definition, gateway/registry/policy engine
- [14] https://www.apideck.com/blog/mcp-server-eating-context-window-cli-alternative — MCP token overhead benchmark, Scalekit 4-32x findings
- [15] https://getunblocked.com/blog/mcp-tool-overload/ — AgentPMT/Hajdas token measurements, Tool Search/Code Mode mitigation results
- [16] https://arxiv.org/pdf/2604.21816 — "Tool Attention" paper: Tools Tax quantification, 95% token reduction result
- [17] https://blog.cloudflare.com/enterprise-mcp/ — Cloudflare enterprise MCP reference architecture, Code Mode portal results
- [18] https://workos.com/blog/mcp-night-block-goose-layered-tool-pattern — Block's layered tool pattern for Square's 200+ endpoint API
- [19] https://stacklok.com/blog/mcp-server-performance-transport-protocol-matters/ — ToolHive Kubernetes transport throughput benchmarks
- [20] https://github.com/JSLEEKR/mcpbench — MCP load-testing tool with p50/p95/p99 CI regression gating
- [21] https://techcommunity.microsoft.com/blog/azure-ai-foundry-blog/load-testing-hosted-mcp-servers-with-locust-and-azure-load-testing/4522691 — Locust/Azure load test of 4 production MCP servers
- [22] https://developers.googleblog.com/scaling-ai-agent-infrastructure-with-the-mcp-stateless-updates/ — Google's account of stateless MCP scaling motivation
- [23] https://blog.cloudflare.com/mcp-v2/ — Cloudflare's account of session-pinning pain and stateless migration
- [24] https://dreaming.press/posts/mcp-server-resume-dropped-session-eventstore.html — EventStore/resumability vs. statelessness trade-off
- [25] https://docs.dapr.io/developing-ai/mcp/mcp-server-resource/ — Dapr MCPServer durable workflow orchestration
- [26] https://docs.temporal.io/ai-cookbook/hello-world-durable-mcp-server — Temporal durable execution pattern for MCP tools
- [27] https://medium.com/@kumaran.isk/enterprise-resilience-patterns-for-mcp-servers-aefba5401bb3 — Circuit breaker/rate limiter/bulkhead/retry patterns for MCP
- [28] https://chatforest.com/guides/mcp-error-handling-resilience/ — MCP error handling, LLM-friendly error messages, per-dependency breakers
- [29] https://mcp-server-langgraph.mintlify.app/guides/resilience-patterns — Combined resilience pattern stack and execution order
- [30] https://www.mcpforge.tech/blog/mcp-zero-trust — Zero Trust MCP 10-principle architecture guide
- [31] https://www.tmdevlab.com/mcp-zero-trust-security-governance.html — NIST 800-207 applied to MCP, OBO/SPIFFE dual verification
- [32] https://www.cerbos.dev/blog/mcp-and-zero-trust-securing-ai-agents-with-identity-and-policy — OAuth Token Exchange (RFC 8693) for scope narrowing
- [33] https://www.coalitionforsecureai.org/wp-content/uploads/2026/03/model-context-protocol-security-1.pdf — CoSAI whitepaper on MCP security model, SPIFFE/SPIRE
- [34] https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization — OAuth 2.1 resource server role, RFC 9728/8414/8707 requirements
- [35] https://imti.co/mcp-authorization-oauth/ — OAuth 2.1 flow walkthrough for MCP (401→metadata→AS discovery→token)
- [36] https://mcp.mintlify.app/specification/2025-11-25/basic/authorization — 2025-11-25 authorization spec mirror
- [37] https://dreaming.press/posts/how-to-authenticate-a-remote-mcp-server.html — Mandatory OAuth 2.1+PKCE for internet-reachable servers (2025-11-25 shift)
- [38] https://xenoss.io/blog/mcp-gateway-architecture-for-enterprise — Gateway vendor landscape (MintMCP, Azure Foundry, Cloudflare)
- [39] https://devcheolu.com/en/posts/kFvkaLLdv8etYRYnWugo — RBAC/audit/token-vault regulatory mapping (SOC2/GDPR/HIPAA/EU AI Act)
- [40] https://www.elegantsoftwaresolutions.com/blog/enterprise-mcp-gateway-implementation-guide — Gateway vendor comparison table (Kong, Azure APIM, Operant)
- [41] https://www.strac.io/blog/mcp-dlp — MCP DLP architecture, inline classification/redaction
- [42] https://arxiv.org/html/2511.20920v1 — "Securing MCP: Risks, Controls, and Governance" academic paper
- [43] https://truto.one/blog/how-to-implement-pii-redaction-when-passing-saas-data-to-llms-via-mcp/ — Zero data retention proxy architecture for PII redaction
- [44] https://mcpmanager.ai/blog/pii-redaction-for-mcp-servers/ — Regex + Presidio + Bedrock Guardrails redaction methods
- [45] https://northflank.com/blog/how-to-sandbox-ai-agents — MicroVM/gVisor/container isolation comparison for AI agent sandboxing
- [46] https://michaellivs.com/blog/sandboxing-ai-agents-2026/ — Anthropic vs. Vercel sandbox architecture comparison, startup/isolation table
- [47] https://dev.to/edison_flores_6d2cd381b13/i-ran-anthropics-official-mcp-server-in-a-gvisor-sandbox-heres-what-happened-a6j — gVisor sandbox test against Anthropic's reference filesystem MCP server
- [48] https://invariantlabs.ai/blog/mcp-security-notification-tool-poisoning-attacks — Original Tool Poisoning Attack disclosure (Invariant Labs, April 2025)
- [49] https://arxiv.org/html/2508.14925 — MCPTox benchmark paper (45 servers, 353 tools, ASR data)
- [50] https://labs.cloudsecurityalliance.org/research/csa-research-note-mcp-tool-poisoning-auto-execution-20260701/ — CSA research note summarizing MCPTox + MCPoison/CurXecute incidents
- [51] https://www.arxiv.org/pdf/2603.22489 — STRIDE/DREAD threat model of MCP, 57 threats, 7-client comparison
- [52] https://arxiv.org/html/2508.12538v1 — MCPLib: 31-method MCP attack taxonomy
- [53] https://www.zealynx.io/blogs/mcp-breach-index-2025-2026 — MCP breach index, 16 incidents Apr 2025–Apr 2026 with CVSS scores
- [54] https://www.practical-devsecops.com/mcp-security-statistics-2026-report/ — MCP security statistics 2026 (adoption stats, CVE details, OAuth adoption %)
- [55] https://stacklok.com/blog/examining-the-impact-of-npm-supply-chain-attacks-on-mcp/ — Sept 2025 npm attack impact on MCP TypeScript SDK dependents
- [56] https://policylayer.com/attacks/compromised-mcp-package — Shai-Hulud worm mechanics and propagation stats
- [57] https://agentlair.dev/blog/mcp-security-vulnerabilities-2026/ — 40+ CVE timeline, OX Security STDIO injection advisory breakdown
- [58] https://www.microsoft.com/en-us/security/blog/2026/05/29/33-malicious-npm-packages-abuse-dependency-confusion-profile-developer-environments/ — Microsoft-disclosed dependency confusion campaign (May 2026)
- [59] https://github.com/vermava/mcp-cve-project — Curated index of 313 MCP-related CVEs, OWASP MCP Top 10 mapping
- [60] https://mcp.directory/blog/npm-supply-chain-attacks-mcp-2026 — npx/uvx "latest" install risk window analysis
- [61] https://chatforest.com/guides/mcp-versioning-backward-compatibility/ — Version negotiation, legacy/modern server detection protocol
- [62] https://www.grizzlypeaksoftware.com/library/mcp-server-versioning-strategies-1ncclykl — MCP server versioning strategies, capability negotiation as version mechanism
- [63] https://modelcontextprotocol.io/specification/draft/basic/versioning — Official versioning/negotiation spec (UnsupportedProtocolVersionError)
- [64] https://hidekazu-konishi.com/entry/mcp_server_testing_and_debugging_guide.html — Schema/handler implementation drift failure mode and testing guidance
- [65] https://thenewstack.io/how-block-got-12000-employees-using-ai-agents-in-two-months/ — Block/Goose 12,000-employee MCP rollout case study
- [66] https://allthingsopen.org/articles/block-scaled-mcp-12000-employees-15-job-functions — Block rollout details, friction-removal strategy
- [67] https://dev.to/goose_oss/mcp-in-the-enterprise-real-world-adoption-at-block-ci5 — Block engineering blog on MCP enterprise adoption
- [68] https://workos.com/blog/mcp-night-block-goose-layered-tool-pattern — Block's layered tool pattern detailed walkthrough
- [69] https://kiadev.net/news/2025-10-08-mcp-function-calling-openapi-when-to-use — MCP vs Function Calling vs OpenAPI decision framework
- [70] https://pikvue.com/mcp-vs-function-calling-vs-openapi-2026-best-ai-tool-integration-protocol-compared/ — 2026 protocol comparison, reliability/adoption data
- [71] https://www.kai-waehner.de/blog/2026/04/10/mcp-vs-rest-http-api-vs-kafka-the-architects-guide-to-agentic-ai-integration/ — MCP vs REST vs Kafka architectural guidance, consistency trade-offs
- [72] https://apiconference.net/blog-en/ai-agents-api-economy-mcp-openapi/ — MCP/OpenAPI coexistence analysis, dual-layer architecture discussion
- [73] https://rajeevbarnwal.medium.com/model-context-protocol-mcp-vs-function-calling-vs-openapi-tools-when-to-use-each-547f3d59c5da — Practitioner decision matrix across latency/observability/governance
- [74] https://a2a-protocol.org/v1.0.0/specification/ — A2A protocol formal specification (canonical data model, layered bindings)
- [75] https://www.glukhov.org/ai-systems/comparisons/a2a-protocol-2026-adoption/ — A2A 2026 adoption analysis, Linux Foundation governance stats
- [76] https://a2a-protocol.org/latest/ — A2A/MCP complementarity explanation (agent-to-agent vs agent-to-tool)
- [77] https://github.com/google/A2A — A2A GitHub repo, Agent Cards, JSON-RPC 2.0 transport, feature list
- [78] https://github.com/modelcontextprotocol/registry/blob/main/docs/modelcontextprotocol-io/about.mdx — Official MCP Registry scope, namespace auth, security-scanning delegation
- [79] https://www.speakeasy.com/mcp/tool-design/generate-mcp-tools-from-openapi/ — OpenAPI-to-MCP generator landscape (Gram, FastMCP, openapi-mcp-generator)
- [80] https://registry.npmjs.org/mcp-openapi — mcp-openapi tool: flat parameter schemas, retry/truncation defaults
