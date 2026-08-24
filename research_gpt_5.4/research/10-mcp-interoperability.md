# Research: MCP & Interoperability - Tools, resources, MCP servers/clients

**Date researched**: 2026-08-21
**Sources consulted**: 7

---

## 1. System Topology & Mechanics

`MCP` is the clearest protocol-level interoperability layer in the local research set: it standardizes exchange of **tools, resources, and prompts** between hosts, clients, and servers over `JSON-RPC 2.0`, rather than binding tool access to any one model vendor or framework (`04-agent-architecture.md`, `03-tool-use.md`). The architectural implication is that the model runtime and the tool-serving surface can evolve independently as long as they preserve the MCP contract (`04-agent-architecture.md`) [inferred].

The local notes also show a meaningful protocol evolution. The older MCP spec is described as **stateful** with capability negotiation during initialization, while the newer 2026 revision is described as **stateless** with self-contained requests and optional async task extensions (`04-agent-architecture.md`). That shift makes MCP easier to load-balance and place behind commodity service infrastructure because request handling depends less on connection affinity (`04-agent-architecture.md`) [inferred].

Framework integration patterns already converge around this split. `OpenAI Agents SDK` uses MCP as an external tool/server integration surface with approval controls, while `CrewAI` documents MCP alongside `A2A`, where MCP is for **host-to-tool/server interoperability** and A2A is for **agent-to-agent delegation** (`05-agent-frameworks.md`, `09-multi-agent-systems.md`). That yields a practical boundary: MCP standardizes access to capabilities; A2A standardizes collaboration among remote agents (`09-multi-agent-systems.md`) [inferred].

The broader interoperability picture is therefore at least three-layered:

- **Native function/tool calling** for vendor-local structured actions (`03-tool-use.md`).
- **MCP** for cross-runtime tool, resource, and prompt access (`03-tool-use.md`, `04-agent-architecture.md`).
- **A2A** for remote delegated agents with their own transport, auth, and lifecycle semantics (`05-agent-frameworks.md`, `09-multi-agent-systems.md`).

Azure's retrieval notes add a useful extension point: a knowledge base can be exposed through either a native `retrieve` action or an `MCP` endpoint, showing that MCP is not limited to imperative tools and can also front permission-aware information systems (`06-rag.md`, `07-memory.md`).

## 2. Token Economics & NFR Metrics

> ⚠️ Limited public data available for **p50/p95/p99 latency**, **raw protocol overhead**, or **apples-to-apples cost benchmarks** specifically for MCP clients and servers in the local research set. The available evidence is stronger on tool-surface overhead, caching, and transport behavior than on MCP-only percentile measurements.

The clearest local cost conclusion is that interoperability does **not** remove the fundamental token economics of tool use. Tool definitions, schemas, prompts, and results still consume context and billing budget whether the tool is native or exposed through MCP (`03-tool-use.md`, `04-agent-architecture.md`). In practice:

```text
interop_run_cost
  ~= model_tokens
   + tool/resource schema tokens
   + tool/result reinjection
   + cache miss penalties
   + transport / hosted-tool surcharges
```

(`03-tool-use.md`, `04-agent-architecture.md`) [inferred]

The local notes give strong support for the idea that **tool-surface size** is itself an NFR concern. OpenAI documents that function definitions count as input tokens, and Anthropic publishes large fixed overhead for browser/computer toolsets, which is a reminder that standardized capability exposure still has prompt-cost consequences (`03-tool-use.md`). MCP can improve interoperability, but it does not make broad tool catalogs free (`03-tool-use.md`) [inferred].

Caching matters more than transport elegance. The surrounding research shows OpenAI and Anthropic both discount cache hits heavily relative to fresh input, and Anthropic explicitly notes that cache-read tokens often do not count against ITPM (`03-tool-use.md`, `04-agent-architecture.md`). For MCP-heavy systems, the most important economic lever is usually keeping stable server metadata, schemas, and repeated instructions cacheable across turns rather than repeatedly rebuilding them (`03-tool-use.md`) [inferred].

For throughput, the local evidence suggests that protocol shape affects the critical path more than absolute token price. Stateful tool loops, repeated approvals, and remote polling can all lengthen end-to-end execution, while stateless request handling and parallel branch execution shorten operational bottlenecks when the architecture permits it (`04-agent-architecture.md`, `09-multi-agent-systems.md`) [inferred].

## 3. Distributed Resilience & State

The strongest resilience takeaway is that `MCP` and `A2A` push agent systems closer to distributed systems than to simple nested function calls. The local notes explicitly describe remote delegation as introducing separate failure domains such as remote endpoints, transport queues, webhook delivery, and coordinator state (`09-multi-agent-systems.md`). The same logic applies to MCP servers once tool access is moved out of process: retries, deadlines, auth refresh, and partial failure handling become first-class concerns (`09-multi-agent-systems.md`, `04-agent-architecture.md`) [inferred].

MCP's move toward **stateless, self-contained requests** is operationally important because it reduces dependence on long-lived connection state at the protocol layer (`04-agent-architecture.md`). That makes horizontal scaling and recovery simpler, but it also means application state, approval state, and conversation continuity must usually live in the host runtime, session layer, or workflow engine above MCP rather than inside the MCP server itself (`04-agent-architecture.md`, `05-agent-frameworks.md`) [inferred].

The local framework notes reinforce that split:

- `OpenAI Agents SDK` uses resumable run state and session persistence around MCP-backed tools rather than presenting MCP itself as the durable state layer (`05-agent-frameworks.md`, `09-multi-agent-systems.md`).
- `CrewAI` places resilience controls such as remote-agent timeout, update mode, and fail-fast behavior around interoperable connections (`09-multi-agent-systems.md`).
- `Azure` retrieval via MCP remains permission-aware, implying that state and authorization policy belong to the backing knowledge system, not just the protocol envelope (`06-rag.md`, `07-memory.md`).

The practical pattern is: keep **workflow state** in sessions/checkpointers/workflow history, and keep **capability access** in interoperable protocols like MCP or A2A (`04-agent-architecture.md`, `05-agent-frameworks.md`) [inferred].

## 4. Enterprise Security & Governance

The local source set is especially strong on `MCP` authorization. For HTTP transports, the notes describe MCP as adopting **OAuth 2.1**, **Protected Resource Metadata** discovery, **Resource Indicators** via the `resource` parameter, and **PKCE with S256**; for `stdio`, credentials should come from the environment instead (`03-tool-use.md`, `04-agent-architecture.md`, `08-planning-reasoning.md`, `09-multi-agent-systems.md`). That is the clearest Zero-Trust interoperability baseline in the local research corpus.

Approval and policy enforcement sit above the protocol boundary. `OpenAI Agents SDK` is repeatedly cited as supporting `needs_approval` and MCP-specific approval requirements, which gives a concrete governance plane for side-effecting external tools (`05-agent-frameworks.md`, `08-planning-reasoning.md`, `09-multi-agent-systems.md`). The defensible pattern is to let a model reason and route freely, but require schema validation, authorization, and optional human approval before external writes occur (`08-planning-reasoning.md`) [inferred].

Interoperability also changes where authorization must be enforced. Azure's knowledge-base notes show that retrieval exposed through MCP still depends on permission-aware backends and role- or key-based access controls (`06-rag.md`, `07-memory.md`). That means protocol interoperability is not sufficient by itself; the backing system must propagate identity and access policy down to the actual data read path (`07-memory.md`) [inferred].

For remote agent interoperability, `A2A` adds another governance surface. `CrewAI` documents Bearer tokens, OAuth2, API keys, HTTP auth, OIDC, mTLS, and agent-card-based discovery for remote agents (`09-multi-agent-systems.md`). Compared with MCP, this broadens the trust boundary from "may I call this tool server?" to "may I delegate work to this independent remote agent over this transport?" (`09-multi-agent-systems.md`) [inferred].

> ⚠️ Limited public data available in the local research set for built-in **PII redaction internals**, **immutable audit-log schemas**, or hard **sandbox isolation guarantees** across interoperable MCP ecosystems. The documented material is much stronger on auth, discovery, approvals, and structured interfaces than on compliance-grade redaction mechanics.

## 5. Production Failure Modes

### Protocol/version skew

The local notes already highlight a non-trivial MCP evolution from stateful initialization-driven semantics to stateless self-contained requests (`04-agent-architecture.md`). Mixed client/server assumptions across those generations can cause subtle interoperability breakage, especially if one side expects negotiated connection state and the other assumes per-request completeness (`04-agent-architecture.md`) [inferred].

### Hallucinated or schema-invalid tool arguments

Structured protocols reduce ambiguity but do not eliminate bad arguments. The local research repeatedly recommends strict schemas, approval gates, or validation/retry loops around tool calls (`03-tool-use.md`, `04-agent-architecture.md`, `08-planning-reasoning.md`, `09-multi-agent-systems.md`). MCP improves the boundary contract; it does not guarantee the model always chooses correct parameters (`04-agent-architecture.md`) [inferred].

### Cascading remote timeouts

Remote interoperability adds latency surfaces that in-process tools avoid. `CrewAI` A2A is documented with timeout controls plus polling/streaming/push update modes, and the OpenAI runtime notes include connection-lifetime constraints for websocket-based execution (`09-multi-agent-systems.md`, `05-agent-frameworks.md`). The safe design is to treat each interoperable server or remote agent as a bulkhead with its own deadline and fallback path (`09-multi-agent-systems.md`) [inferred].

### Authorization misconfiguration

Because MCP authorization depends on discovery metadata, PKCE, and resource-bound tokens, mistakes in server metadata, scope handling, or resource binding can fail requests or accidentally broaden privilege if implemented poorly (`04-agent-architecture.md`, `08-planning-reasoning.md`, `09-multi-agent-systems.md`). This is a classic interoperability failure mode: the protocol is standardized, but the security posture still depends on correct end-to-end implementation [inferred].

### Hidden state and observability gaps

The local architecture notes argue that tool protocols and workflow state should be separated (`04-agent-architecture.md`). If teams blur those layers, debugging becomes harder because request/response interoperability may look healthy while approval state, session state, or workflow history above it has drifted (`04-agent-architecture.md`, `05-agent-frameworks.md`) [inferred].

### Incident coverage

> ⚠️ Limited public data available for detailed RCA-style incident reports focused specifically on MCP clients, MCP servers, and mixed-protocol production outages in the local research set.

## 6. Enterprise System Design Scenarios

### 6.1 Interoperability pattern matrix

| Pattern | Best fit | Strongest benefits | Main trade-offs |
| --- | --- | --- | --- |
| Native function tools | Single-vendor stacks with stable internal APIs | Lowest protocol complexity; strongest vendor-local schema support (`03-tool-use.md`) | Weakest portability across runtimes [inferred] |
| `MCP` tool/resource servers | Shared enterprise tools, knowledge systems, cross-framework capability reuse | Standardized discovery/auth/contracts for tools, resources, and prompts (`03-tool-use.md`, `04-agent-architecture.md`) | Requires careful auth, schema, and server lifecycle management |
| `A2A` remote agents | Cross-team or cross-vendor delegation where the remote side owns its own reasoning/runtime | Agent cards, transport choice, remote auth, and partial independence (`05-agent-frameworks.md`, `09-multi-agent-systems.md`) | Highest distributed-systems complexity, timeout risk, and observability burden |

### 6.2 Recommended deployment patterns

**Pattern A: Enterprise copilot with many internal systems**

Use `MCP` to standardize access to internal tools and knowledge resources, but keep approvals, session state, and policy enforcement in the host agent runtime (`05-agent-frameworks.md`, `08-planning-reasoning.md`, `09-multi-agent-systems.md`). This keeps interoperability broad without forcing business control logic into every server [inferred].

**Pattern B: Permission-aware retrieval platform**

Expose retrieval through MCP only if the underlying knowledge system already enforces role/key-based authorization and returns references/activity logs or equivalent audit surfaces (`06-rag.md`, `07-memory.md`, `08-planning-reasoning.md`). Interoperability is valuable here because multiple agents can share one governed retrieval plane [inferred].

**Pattern C: Cross-organization automation**

Use `A2A` when the remote party should own an agent endpoint, transport, and execution lifecycle; use `MCP` when the remote party is exposing bounded capabilities or resources into your host runtime (`05-agent-frameworks.md`, `09-multi-agent-systems.md`). That separation prevents teams from overusing full remote delegation when standardized tool access would be simpler and safer [inferred].

### 6.3 Capacity-planning heuristics

Useful first-order formulas synthesized from the local notes:

```text
critical_path_latency
  ~= routing/planning
   + auth/discovery overhead
   + max(remote branch durations)
   + approvals
   + result synthesis
```

```text
interop_reliability
  improves when workflow state
  is stored outside protocol sessions
  and each remote boundary has its own timeout/retry policy
```

(`04-agent-architecture.md`, `05-agent-frameworks.md`, `08-planning-reasoning.md`, `09-multi-agent-systems.md`) [inferred]

### 6.4 Strongest practical conclusions

1. `MCP` is the strongest protocol in the local research set for **host-to-tool/resource interoperability**, not for replacing workflow orchestration (`03-tool-use.md`, `04-agent-architecture.md`).
2. `A2A` and `MCP` solve different problems: **remote agent delegation** versus **standardized capability access** (`05-agent-frameworks.md`, `09-multi-agent-systems.md`).
3. The security center of gravity is not the protocol alone but the combination of **OAuth/PKCE/resource binding**, **schema rigor**, and **approval policy** (`04-agent-architecture.md`, `08-planning-reasoning.md`, `09-multi-agent-systems.md`).
4. The cleanest production architecture keeps **state and governance above the interoperability layer**, while keeping tools/resources below it (`04-agent-architecture.md`, `05-agent-frameworks.md`) [inferred].

## Sources

- [1] `03-tool-use.md` - Local research note covering function calling, hosted tools, MCP transport/auth basics, tool-surface token costs, and validation risks.
- [2] `04-agent-architecture.md` - Local research note covering MCP protocol evolution, control-plane/data-plane separation, schema enforcement, and architecture-level failure modes.
- [3] `05-agent-frameworks.md` - Local research note comparing framework integration of MCP, A2A, sessions, approvals, and persistence layers.
- [4] `06-rag.md` - Local research note covering permission-aware retrieval and MCP exposure for knowledge bases.
- [5] `07-memory.md` - Local research note covering authorization-aware memory/retrieval access and separation of memory read/write control planes.
- [6] `08-planning-reasoning.md` - Local research note covering MCP-linked governance patterns, approvals, and guarded execution.
- [7] `09-multi-agent-systems.md` - Local research note covering A2A delegation, agent cards, auth choices, timeout surfaces, and MCP versus agent-to-agent boundaries.
