# Module 17 — Design Personal AI Chat Assistant

**Audience**: Principal AI Architect interview prep · personal deep study  
**Sequence**: 17 (product-level composition of streaming chat, memory, tools, safety, multi-device sync)  
**Grounded in**: `research/17-personal-ai-chat-assistant.md` (28 sources, 2026-09-30)

A personal / enterprise AI chat assistant is not “wrap ChatGPT.” It is a product that owns **auth, conversation history, system prompt, cost levers, injection defenses, and output policy** while composing provider streaming APIs, optional tools, and multi-device sync ([System Design Newsletter #144](https://newsletter.systemdesign.one/p/ai-chat-assistant)). Pure conversational build: multi-turn context, SSE streaming, decoding knobs, injection resistance, cost control at thousands of conversations/day. Tools, RAG, and long-term memory are additive layers — not required for the base path ([#144](https://newsletter.systemdesign.one/p/ai-chat-assistant)).

---

## Part 1 — System Topology & Data Flow

### Architecture map

```
                         ┌──────────────────────────────────────────────────────────┐
                         │                    CONTROL PLANE                         │
                         │  API gateway · SSO/OIDC · rate limit · model router      │
                         │  conversation CRUD · policy · moderation gate            │
                         │  session / revision lock · active_leaf_id                │
                         │                                                          │
                         │  ┌────────────┐  ┌────────────┐  ┌────────────────────┐  │
                         │  │ AuthN/Z    │  │ Model      │  │ Compaction /       │  │
                         │  │ + quotas   │  │ router     │  │ memory write gate  │  │
                         │  └─────┬──────┘  └─────┬──────┘  └─────────┬──────────┘  │
                         └────────┼───────────────┼───────────────────┼─────────────┘
                                  │               │                   │
                                  ▼               ▼                   ▼
                         ┌──────────────────────────────────────────────────────────┐
                         │                     DATA PLANE                           │
                         │  Context assemble → prefill + decode → SSE/WS stream     │
                         │  Prompt builder: system + history + memory + tool schemas│
                         │  Stream gateway: flush deltas; fan-out via pub/sub       │
                         └───┬──────────────────────┼───────────────────────┬───────┘
                             │                      │                       │
              ┌──────────────┴───────┐  ┌───────────┴─────────┐  ┌──────────┴───────┐
              │     TOOL PROXIES     │  │    PERSISTENCE      │  │    TELEMETRY     │
              ├──────────────────────┤  ├─────────────────────┤  ├──────────────────┤
              │  MCP tool servers    │  │  Conversation DB    │  │  TTFT / TBOT     │
              │  (calendar, email,   │  │  (canonical turns)  │  │  tokens · $ · RPM│
              │   CRM) · schema      │  │  Redis hot window    │  │  cache hit rate  │
              │  validate · HITL     │  │  Memory / profile   │  │  breaker state   │
              │  vault-held secrets  │  │  Pub/sub channel    │  │  correlation IDs │
              └──────────────────────┘  └─────────────────────┘  └──────────────────┘
```

**Plane responsibilities**

| Plane | Role in a chat assistant |
| --- | --- |
| **CONTROL PLANE** | AuthN/Z, conversation CRUD, policy, model routing, rate limits, compaction/memory write gates ([#144](https://newsletter.systemdesign.one/p/ai-chat-assistant); [OpenAI ILB framing](https://finance.biggo.com/podcast/afa4b13a445a4639)) |
| **DATA PLANE** | Context assembly, provider prefill/decode, SSE/WebSocket token delivery, pub/sub fan-out ([OpenAI Streaming](https://developers.openai.com/api/docs/guides/streaming-responses); [Ably session continuity](https://ably.com/blog/ai-session-continuity-cross-device-channel-based-architecture)) |
| **PERSISTENCE** | Server-canonical transcript (Postgres), hot resume cache, personal memory profile, channel sequence for catch-up ([Conversation state](https://developers.openai.com/api/docs/guides/conversation-state); [ByteByteGo chat sync](https://bytebytego.com/courses/system-design-interview/design-a-chat-system)) |
| **TOOL PROXIES** | MCP/function tools with `strict` schemas, app-held credentials, HITL for irreversible actions ([Function calling](https://platform.openai.com/docs/guides/function-calling); OWASP) |
| **TELEMETRY** | Client TTFT (first text delta), token/$, RPM/TPM headers, cache hit %, circuit-breaker state, correlation IDs |

### End-to-end request-flow narrative

1. **Client → session** — Mobile/web client sends a user turn with `conversation_id`, client `message_id` (idempotency key), and auth token. CONTROL PLANE authenticates (OIDC), enforces per-user RPM/TPM quotas, and opens or resumes the conversation revision / `active_leaf_id` ([#144](https://newsletter.systemdesign.one/p/ai-chat-assistant); [AI Systems — chat tree](https://ikshitij.com/learn/ai-systems/chatgpt-chat-app/)).
2. **Persist user turn first** — Append the user message to the **server-canonical** conversation store *before* inference so a crashed stream never loses the ask ([inferred] from streaming lifecycle + multi-device durability; research §3).
3. **Context assemble** — DATA PLANE builds: system prompt + tool schemas (stable prefix for prompt cache) + last-K turns and/or compaction item + optional memory/RAG snippets. Token budget gates decide long window vs compact vs retrieve ([Conversation state](https://developers.openai.com/api/docs/guides/conversation-state); [Compaction](https://developers.openai.com/api/docs/guides/compaction); [Prompt Caching](https://developers.openai.com/api/docs/guides/prompt-caching)).
4. **Input guard → model call** — Moderation / injection classifiers on the user text; model router picks frontier vs mini. Provider called with `stream=true` (Responses SSE events or Chat Completions `delta.content`) ([OpenAI Streaming](https://developers.openai.com/api/docs/guides/streaming-responses); [Moderation](https://developers.openai.com/api/docs/guides/moderation)).
5. **Stream tokens** — Stream gateway flushes each text delta to the originating SSE connection *and* publishes to the conversation channel so other devices can subscribe. **Do not buffer** the proxy — buffering destroys TTFT ([DigitalOcean — p99 TTFT](https://www.digitalocean.com/community/tutorials/debugging-p99-ttft-llm-inference); [Ably](https://ably.com/blog/ai-session-continuity-cross-device-channel-based-architecture)).
6. **Optional tool loop** — On `function_call` / tool-use blocks, TOOL PROXIES validate schema, enforce RBAC, execute with vault credentials, return results into the next model step; side effects wait until output moderation when risk is high ([Function calling](https://platform.openai.com/docs/guides/function-calling); [Streaming moderation note](https://developers.openai.com/api/docs/guides/streaming-responses)).
7. **Persist turn** — On `response.completed` / `message_stop`, write the assistant message (or mark partial + `interrupted`). Emit TELEMETRY: TTFT, tokens in/out, cache hit, `$`, correlation id. Reconnecting devices catch up via `cur_max_message_id` / sequence, then live-subscribe — **plain SSE cannot resume mid-stream across devices** ([Ably](https://ably.com/blog/ai-session-continuity-cross-device-channel-based-architecture); [ByteByteGo](https://bytebytego.com/courses/system-design-interview/design-a-chat-system)).

Message style: **synchronous turn loop with durable appends**; live tokens are ephemeral fan-out over a connection-scoped stream plus a channel; history is always reloaded from the server.

---

## Part 2 — Core Mechanics & Algorithms

### Theoretical fundamentals

Inference remains next-token prediction. **Prefill** processes the full prompt; **decode** emits tokens autoregressively with a KV-cache of prior keys/values. Longer history raises both latency and cost: prefill work and billed input tokens grow every turn ([#144](https://newsletter.systemdesign.one/p/ai-chat-assistant); [Prompt Caching](https://developers.openai.com/api/docs/guides/prompt-caching) — caches reuse KV states).

Models operate on **subword tokens** (commonly BPE), not words. Rough English: **10 words ≈ 13–15 tokens**; code and many non-English languages are denser. Character-level tasks fail when letters split across token boundaries. Pricing, context limits, and TTFT all scale with token count ([#144](https://newsletter.systemdesign.one/p/ai-chat-assistant)). Post-training (SFT then RLHF/preference optimization) is what makes a base LM follow system prompts and refuse some harmful asks ([#144](https://newsletter.systemdesign.one/p/ai-chat-assistant)).

### Conversation-state patterns (OpenAI)

| Pattern | Semantics | Retention / billing note |
| --- | --- | --- |
| Manual transcript | App persists turns; resends full `messages` / `input` | App owns durability |
| `previous_response_id` | Pass only new user turn; provider threads prior response | Responses retained **30 days** by default (`store: false` disables); **prior input tokens in the chain are still billed** ([Conversation state](https://developers.openai.com/api/docs/guides/conversation-state)) |
| Conversations API | Durable `conversation_id` across sessions/devices/jobs | Conversation items **not** subject to 30-day response TTL ([same](https://developers.openai.com/api/docs/guides/conversation-state)) |

Long windows: server-side **compaction** (`context_management` / `compact_threshold`, example **200,000** tokens) emits an opaque compaction item; standalone `POST /responses/compact` supports ZDR-friendly `store=false` flows ([Compaction](https://developers.openai.com/api/docs/guides/compaction)).

### Streaming event models

- **OpenAI Responses**: `response.created` → `response.output_text.delta`* → `response.completed` / `error`; tool args via `response.function_call_arguments.delta` ([Streaming](https://developers.openai.com/api/docs/guides/streaming-responses)).
- **Chat Completions**: `data:` JSON with `choices[].delta.content`, terminated by `data: [DONE]` ([same](https://developers.openai.com/api/docs/guides/streaming-responses)).
- **Anthropic Messages**: `message_start` → (`content_block_start` → `content_block_delta`* → `content_block_stop`)* → `message_delta` → `message_stop`. Measure text TTFT on first `content_block_delta` with `delta.type == text_delta`, **not** on `message_start` ([Anthropic Streaming](https://platform.claude.com/docs/en/build-with-claude/streaming); [Reducing latency](https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/reduce-latency)).

### Turn state machine

```
     ┌──────────┐   accept +   ┌────────────┐   assemble   ┌────────────┐
     │  IDLE    │──persist───►│ USER_TURN  │─────────────►│  PREFILL   │
     └──────────┘  message_id  └────────────┘              └─────┬──────┘
           ▲                                                     │ first text delta
           │                                                     ▼
           │              ┌────────────┐   completed    ┌────────────┐
           │              │ PERSISTED  │◄───────────────│  DECODING  │──tool?──► TOOL_LOOP
           │              └──────┬─────┘                └─────┬──────┘
           │                     │                            │ disconnect / error
           │                     ▼                            ▼
           │              ┌────────────┐                ┌────────────┐
           └──────────────│  COMPLETE  │                │ INTERRUPTED│
                          └────────────┘                │ (partial)  │
                                                        └────────────┘
```

**Invariants**

1. Client `message_id` is idempotent: duplicate submit returns the same turn, does not double-bill or double-append.  
2. User turn is durable before the first provider byte.  
3. Assistant turn is either `completed`, `interrupted` (partial persisted), or absent — never silently dropped.  
4. Multi-device truth is the conversation store + sequence, not the SSE socket.

### Prompt-cache algorithm (complexity)

Stable prefix (system + tools + schemas) of length \(P\), variable suffix (history + user) of length \(S\):

- Uncached prefill ≈ \(O(P+S)\) attention work and full input billing.  
- Cache hit on exact prefix match: billed cached-input rate on \(P\); TPM still counts full input ([Prompt Caching](https://developers.openai.com/api/docs/guides/prompt-caching); [Community — cache ≠ TPM relief](https://community.openai.com/t/does-prompt-caching-reduce-tpm/1138631)).  
- Prefix must match exactly — tools, structured-output schemas, reasoning effort, verbosity, and compaction all bust the cache ([Prompt Caching](https://developers.openai.com/api/docs/guides/prompt-caching)).  
- Cookbook heuristic: ~**15 RPM** per `(prefix + prompt_cache_key)` before load-balancing causes one-time misses ([Prompt Caching 201](https://developers.openai.com/cookbook/examples/prompt_caching_201)).

---

## Part 3 — Token Economics & NFR Analysis

### Cost formulas — `$` per 1k turns (GPT-4o)

**Assumptions (labeled)**

| Parameter | Value | Source |
| --- | --- | --- |
| Model | GPT-4o | [OpenAI GPT-4o](https://developers.openai.com/api/docs/models/gpt-4o) |
| Input | $2.50 / 1M tokens | same |
| Cached input | $1.25 / 1M tokens | same |
| Output | $10.00 / 1M tokens | same |
| Tokens / turn | 5,000 input + 1,400 output | research toy support turn ([research §2](../research/17-personal-ai-chat-assistant.md)) |
| Cache scenario A | 0% input cached | baseline |
| Cache scenario B | 100% of the 5k input at cached rate | upper-bound saving (stable prefix + warm cache) |

**Per-turn arithmetic**

\[
\begin{align*}
C_{\text{uncached}} &= 5000 \times \$2.50 \times 10^{-6} + 1400 \times \$10 \times 10^{-6} \\
&= \$0.0125 + \$0.0140 = \$0.0265 \\
C_{\text{100\% cached in}} &= 5000 \times \$1.25 \times 10^{-6} + 1400 \times \$10 \times 10^{-6} \\
&= \$0.00625 + \$0.0140 = \$0.02025
\end{align*}
\]

**Per 1k turns**

| Scenario | Formula | **$ / 1k turns** |
| --- | --- | ---: |
| GPT-4o, uncached | \(1000 \times \$0.0265\) | **$26.50** |
| GPT-4o, 100% input cached @ $1.25/1M | \(1000 \times \$0.02025\) | **$20.25** |

At **15,000** turns/month (research toy: 500 conversations/day × ~1 turn average): uncached ≈ **$398**/month; strong cache hits cut the input line ~**50%** on GPT-4o’s cached rate ([research arithmetic](../research/17-personal-ai-chat-assistant.md); [GPT-4o pricing](https://developers.openai.com/api/docs/models/gpt-4o)).

Comparators from research (same 5k/1.4k shape): GPT-4.1 uncached **$0.0212**/turn; Claude Sonnet 4.5 uncached **$0.0360**/turn ([GPT-4.1](https://developers.openai.com/api/docs/models/gpt-4.1); [Anthropic Prompt Caching](https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching)).

### Latency SLA targets

> ⚠️ **Gap — published TTFT SLAs**: OpenAI and Anthropic do **not** publish contractual p50 / p95 / p99 TTFT for shared chat APIs. Secondary aggregates (e.g. GPT-4o ~200–400 ms US-East on dedicated capacity) are **not** treated as benchmarks here ([research §2](../research/17-personal-ai-chat-assistant.md)).

**[inferred] latency budget (ms)** — design targets for a streaming personal assistant, not vendor SLAs. Measure TTFT client-side on first non-empty **text** delta ([Anthropic Reducing latency](https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/reduce-latency)).

| Tier | TTFT budget | Decode budget (1.4k tokens @ ~40 tok/s) | E2E to last token | Dominant causes | Mitigations |
| --- | ---: | ---: | ---: | --- | --- |
| **[inferred] p50** | **≤ 400 ms** | ≈ \(1400/40 \times 1000 = 35{,}000\) ms | ≈ **35.4 s** | Warm cache, short queue | Streaming (paint at TTFT); stable prompt-cache prefix; unbuffered SSE proxy |
| **[inferred] p95** | **≤ 1{,}200 ms** | same decode floor + jitter | ≈ **36.2 s** | Cold prefix, light queue | Prompt cache; keep tools/schemas stable; route FAQ → mini |
| **[inferred] p99** | **≤ 3{,}000 ms** | queue + KV alloc spikes | ≈ **38+ s** | Queueing, routing, KV allocation, proxy buffering | Capacity headroom; shed to smaller model; never buffer stream gateway ([DigitalOcean p99 TTFT](https://www.digitalocean.com/community/tutorials/debugging-p99-ttft-llm-inference)) |

**Streaming arithmetic (why stream)**: without streaming, user waits full TTFT+decode (~35 s p50 above). With streaming, **time-to-first-paint ≈ TTFT**; remaining decode is progressive. Perceived “instant” band often cited ~**<500 ms** TTFT — design heuristic, not an OpenAI SLA ([research](../research/17-personal-ai-chat-assistant.md)).

### Throughput & back-pressure

OpenAI limits are multi-dimensional (RPM, TPM, RPD, …); whichever trips first returns `429` + `Retry-After` / `x-ratelimit-*` ([Rate limits](https://developers.openai.com/api/docs/guides/rate-limits)).

| Tier (published examples) | RPM | TPM |
| --- | ---: | ---: |
| Build | 5,000 | 1,000,000 |
| Launch | 10,000 | 4,000,000 |
| Grow | 15,000 | 40,000,000 |

**Critical invariant**: **cache hits do not reduce TPM rate-limit accounting** — TPM is enforced before cache lookup ([OpenAI Community](https://community.openai.com/t/does-prompt-caching-reduce-tpm/1138631); [Rate limits](https://developers.openai.com/api/docs/guides/rate-limits)). Plan capacity on raw input+output tokens, not on post-cache billable dollars.

Back-pressure design:

1. Token-bucket / sliding window per `(user_id, tier)` at the gateway.  
2. Honor `Retry-After`; exponential backoff + jitter toward the provider.  
3. When RPM-bound but TPM-rich, batch; when TPM-bound, shed load or route to mini.  
4. Ramp rule of thumb near ~**1M** input TPM: increase ≤ **50%** every **15 minutes** ([Rate limits](https://developers.openai.com/api/docs/guides/rate-limits)).  
5. Fallback chain: frontier → mid → mini → template refusal (see Part 4 / Part 5).

### Non-functional requirements & trade-offs

| NFR | Target / stance | Notes |
| --- | --- | --- |
| **Availability** | Product chat API **99.9%** monthly [inferred product target]; degrade with fallback models rather than hard-fail | Provider 503/429 are expected; circuit breaker + model chain |
| **RPO (conversation history)** | **0** for acknowledged user turns (sync append before inference) | Redis is hot cache only — not SoR |
| **RTO (history restore)** | **< 60 s** to serve history from primary or replica after DB failover [inferred ops target] | Multi-device resume = history catch-up + channel subscribe |
| **Compliance** | SSO/OIDC, per-user isolation, deletable stores (GDPR), BAA-capable providers where HIPAA; TLS + AES-at-rest as checklist | Your build does **not** inherit ChatGPT Enterprise attestations ([OpenAI trust/pricing](https://openai.com/api/pricing/); [#144](https://newsletter.systemdesign.one/p/ai-chat-assistant)) |

**Explicit context trade-off — long context vs compaction vs memory retrieval**

| Strategy | Cost | Latency | Fidelity | When to use |
| --- | --- | --- | --- | --- |
| **Long context** (send full history) | Highest input $ + prefill TTFT | Worst TTFT as \(S\) grows | Best literal recall of recent turns | Short threads; debugging |
| **Compaction** (opaque / server compact item) | Lower ongoing input; compact op cost | Better TTFT after prune | Opaque — don’t invent human summaries as substitute | Crosses compact_threshold (~200k example) ([Compaction](https://developers.openai.com/api/docs/guides/compaction)) |
| **Memory retrieval** (profile / RAG snippets) | Bounded retrieve + small inject | Prefill stays small | Lossy; ACL/FGA required | Cross-session preferences & product knowledge ([#144](https://newsletter.systemdesign.one/p/ai-chat-assistant)) |

**Decision rule**: keep last-K literal turns for local coherence; compact or summarize when approaching window; retrieve durable facts from a memory/RAG store instead of infinite history. Realtime voice windows (32,768 total, max 4,096 out) make this trade-off acute — use `retention_ratio` (e.g. 0.8) to amortize cache busts ([Realtime](https://developers.openai.com/blog/realtime-api)).

---

## Part 4 — Distributed Resilience & Security

### Durable execution — server-canonical history

| Store | Role |
| --- | --- |
| App DB (Postgres) | **Canonical** transcript, branches, `active_leaf_id` — required for multi-device and compliance deletes |
| OpenAI Conversations | Optional provider-durable items (no 30-day TTL on conversation items) |
| `previous_response_id` | Soft chain; 30-day response retention; broken ID → resend full context |
| Redis | Last-N + summary for fast resume — **not** source of truth |
| Pub/sub channel | Live token fan-out + history replay on reconnect |

Write path: authenticate → rate-limit → **append user message (durable)** → assemble context → stream model → persist assistant turn → fan-out. Prefer append-only message logs for auditability ([Conversation state](https://developers.openai.com/api/docs/guides/conversation-state); [Ably](https://ably.com/blog/ai-session-continuity-cross-device-channel-based-architecture)).

**Plain SSE cannot resume mid-stream across devices**: a new device has no stream cursor. Production pattern = server-canonical conversation + channel/pub-sub; reconnect = history catch-up then live subscribe ([Ably](https://ably.com/blog/ai-session-continuity-cross-device-channel-based-architecture)).

Optimistic concurrency on conversation revision / `active_leaf_id` prevents silent forks when two devices edit/regenerate ([AI Systems — tree](https://ikshitij.com/learn/ai-systems/chatgpt-chat-app/)).

### Failure taxonomy

| Class | Examples | Detection | Mitigation |
| --- | --- | --- | --- |
| **Transient** | 429, 503, network blip, idle stream timeout | Status codes, `Retry-After`, missing heartbeats | Backoff + jitter; honor Retry-After; circuit breaker |
| **Permanent** | 400 schema, auth fail, policy deny | Non-retryable status / validation | Fail closed to client; do not retry |
| **Poison pill** | Repeated identical tool args looping; malformed tool JSON that always fails | Duplicate-call detector; max iterations / $ cap | Hard stop; `parallel_tool_calls: false`; reject + correct ([Function calling](https://platform.openai.com/docs/guides/function-calling)) |
| **Partial stream** | Disconnect before `completed` / `message_stop` | Missing terminal event; sequence gaps | Persist partial + `interrupted`; channel replay ([Ably](https://ably.com/blog/ai-session-continuity-cross-device-channel-based-architecture)) |
| **State drift** | Two devices fork branches | Version / leaf mismatch | Single-writer or merge UI |
| **TPM despite cache** | 429 while bills look “cached-cheap” | Rate-limit headers | Caching ≠ TPM relief |

### IDEMPOTENCY of client message ids

Client generates a UUID `message_id` per user submit. Server unique-constrains `(conversation_id, message_id)`:

- First insert → process turn.  
- Duplicate → return existing turn / in-flight stream handle; **no second provider call**.  

This is the chat analogue of payment idempotency keys and is required for flaky mobile networks and double-tap send.

### Circuit breaker — closed → open → half-open

```
                    failures ≥ N
         ┌──────────┐ ──────────────► ┌──────────┐
         │  CLOSED  │                 │   OPEN   │──cool-down──►┌────────────┐
         └──────────┘ ◄──success──┐   └──────────┘              │ HALF-OPEN  │
              ▲                   │                             └─────┬──────┘
              │                   │          trial success            │ trial fail
              └───────────────────┴───────────────────────────────────┘
```

| State | Behavior |
| --- | --- |
| **Closed** | All calls to primary model; count consecutive failures |
| **Open** | Fail fast (or skip) to fallback chain for cool-down window |
| **Half-open** | Allow a probe request; success → closed; failure → open |

Apply breakers **per dependency** (primary LLM, secondary LLM, each tool bulkhead) with separate timeouts ([research §3](../research/17-personal-ai-chat-assistant.md)).

### Fallback model chain

Graceful chain: **frontier → mid → mini → deterministic template** (refusal / “try again later”). Keep system-prompt prefix stable across tiers where possible to preserve cache ([#144](https://newsletter.systemdesign.one/p/ai-chat-assistant); [Prompt Caching](https://developers.openai.com/api/docs/guides/prompt-caching)).

### Zero-Trust MCP for tools

Treat every MCP / function tool as an untrusted capability boundary:

1. **Authenticate** the calling user/session; never pass provider API keys into the model context ([#144](https://newsletter.systemdesign.one/p/ai-chat-assistant)).  
2. **Authorize** tool name + args against RBAC / FGA (tenant, role, resource ACL) before execution.  
3. **Validate** JSON against `strict: true` schemas; reject invented ids ([Function calling](https://platform.openai.com/docs/guides/function-calling)).  
4. **Isolate** credentials in a vault / tool proxy — model sees only tool names and sanitized results.  
5. **HITL** for irreversible / money-moving tools (Rule of Two: untrusted input × sensitive data × state change) ([OWASP Prompt Injection Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html)).

### Tool RBAC

Allowlist tools per role (`tool_choice` / allowed-tools subset). Default deny. Separate read tools (calendar get) from write tools (send email, refund). FGA over RAG corpora and tools for multi-tenant copilots ([#144](https://newsletter.systemdesign.one/p/ai-chat-assistant) partner FGA framing; [Function calling](https://platform.openai.com/docs/guides/function-calling)).

### PII pipeline — detect → redact → audit (before persistence)

```
  user/assistant text ──► DETECT (regex + NER / classifiers)
                         ──► REDACT / tokenize for logs & analytics sinks
                         ──► AUDIT event (what class redacted, where, by whom)
                         ──► PERSIST policy-compliant form to conversation SoR
```

Retention ≠ training consent. GDPR/HIPAA imply deletable conversation stores and BAA-capable providers where applicable ([research §4](../research/17-personal-ai-chat-assistant.md)).

### Immutable turn log & guard boundary

- **Immutable turn log**: append-only records of `user_id`, `conversation_id`, `message_id`, model, token usage, tool name + args hash, moderation flags, policy decisions, `request_id` / provider ids — SOC2 evidence trail ([research §4](../research/17-personal-ai-chat-assistant.md)).  
- **Input / output guard boundary**: classifiers on ingress (`omni-moderation-latest` or equivalent); output moderation before committing tool side effects. Streaming makes partial moderation harder — delay irreversible effects until full-output check when risk is high ([Moderation](https://developers.openai.com/api/docs/guides/moderation); [Streaming](https://developers.openai.com/api/docs/guides/streaming-responses); [OWASP LLM Top 10](https://genai.owasp.org/)).

---

## Part 5 — Production Enterprise Code

Runnable, deterministic Python: fake token stream (no network, no API keys), client `message_id` idempotency, retries with exponential backoff + jitter, circuit breaker (`closed → open → half-open`), fallback model chain, correlation IDs. Run: `python 17_chat_turn_loop.py` (or paste into a file).

```python
"""Deterministic personal-chat turn loop — no API keys, no network."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Iterator, List, Optional
import hashlib


# --- Deterministic clock / RNG (no wall-clock, no secrets) -------------------

class DetClock:
    def __init__(self) -> None:
        self._ms = 0

    def now_ms(self) -> int:
        return self._ms

    def advance(self, ms: int) -> None:
        self._ms += ms


def det_jitter_ms(correlation_id: str, attempt: int, cap_ms: int = 50) -> int:
    """Bounded jitter from correlation_id + attempt — reproducible."""
    h = hashlib.sha256(f"{correlation_id}:{attempt}".encode()).hexdigest()
    return int(h[:8], 16) % (cap_ms + 1)


# --- Circuit breaker ---------------------------------------------------------

class BreakerState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    failure_threshold: int = 2
    cool_down_ms: int = 100
    clock: DetClock = field(default_factory=DetClock)
    state: BreakerState = BreakerState.CLOSED
    failures: int = 0
    opened_at_ms: Optional[int] = None

    def allow(self) -> bool:
        if self.state == BreakerState.CLOSED:
            return True
        if self.state == BreakerState.OPEN:
            assert self.opened_at_ms is not None
            if self.clock.now_ms() - self.opened_at_ms >= self.cool_down_ms:
                self.state = BreakerState.HALF_OPEN
                return True
            return False
        return True  # HALF_OPEN: one probe

    def record_success(self) -> None:
        self.failures = 0
        self.state = BreakerState.CLOSED
        self.opened_at_ms = None

    def record_failure(self) -> None:
        self.failures += 1
        if self.state == BreakerState.HALF_OPEN or self.failures >= self.failure_threshold:
            self.state = BreakerState.OPEN
            self.opened_at_ms = self.clock.now_ms()
            self.failures = 0


# --- Fake models -------------------------------------------------------------

class TransientProviderError(Exception):
    pass


@dataclass
class FakeModel:
    name: str
    fail_times: int = 0  # how many calls fail before succeeding
    _calls: int = 0

    def stream(self, prompt_tokens: int) -> Iterator[str]:
        self._calls += 1
        if self._calls <= self.fail_times:
            raise TransientProviderError(f"{self.name} transient failure #{self._calls}")
        # Deterministic fake tokens from prompt length
        words = [f"tok{i}" for i in range(min(5, 1 + prompt_tokens // 1000))]
        for w in words:
            yield w + " "


# --- Idempotent conversation store ------------------------------------------

@dataclass
class Turn:
    message_id: str
    role: str
    content: str
    model: str
    correlation_id: str
    status: str  # completed | interrupted


@dataclass
class ConversationStore:
    turns: Dict[str, Turn] = field(default_factory=dict)  # message_id -> turn
    order: List[str] = field(default_factory=list)

    def get(self, message_id: str) -> Optional[Turn]:
        return self.turns.get(message_id)

    def append(self, turn: Turn) -> Turn:
        existing = self.turns.get(turn.message_id)
        if existing is not None:
            return existing
        self.turns[turn.message_id] = turn
        self.order.append(turn.message_id)
        return turn


# --- Chat turn loop ----------------------------------------------------------

@dataclass
class ChatTurnLoop:
    store: ConversationStore
    primary: FakeModel
    fallbacks: List[FakeModel]
    breaker: CircuitBreaker
    clock: DetClock
    max_retries: int = 3
    base_backoff_ms: int = 10
    log: List[str] = field(default_factory=list)

    def _log(self, correlation_id: str, msg: str) -> None:
        self.log.append(f"cid={correlation_id} t={self.clock.now_ms()} {msg}")

    def _chain(self) -> List[FakeModel]:
        return [self.primary, *self.fallbacks]

    def run_user_turn(
        self,
        *,
        message_id: str,
        user_text: str,
        correlation_id: str,
        prompt_tokens: int = 5000,
    ) -> Turn:
        # Idempotency: duplicate client message_id returns prior result
        existing = self.store.get(message_id)
        if existing is not None:
            self._log(correlation_id, f"idempotent_hit message_id={message_id}")
            return existing

        # Persist user turn before inference
        user_turn = Turn(
            message_id=f"user:{message_id}",
            role="user",
            content=user_text,
            model="n/a",
            correlation_id=correlation_id,
            status="completed",
        )
        self.store.append(user_turn)
        self._log(correlation_id, f"persisted_user message_id={message_id}")

        last_err: Optional[Exception] = None
        for model in self._chain():
            is_primary = model.name == self.primary.name
            # Breaker guards primary only; OPEN skips primary and uses fallback chain
            if is_primary and not self.breaker.allow():
                self._log(correlation_id, f"breaker_open skip_primary model={model.name}")
                continue

            for attempt in range(self.max_retries):
                if is_primary and not self.breaker.allow():
                    self._log(correlation_id, f"breaker_open abort_retries model={model.name}")
                    break
                try:
                    self._log(
                        correlation_id,
                        f"attempt model={model.name} n={attempt} breaker={self.breaker.state.value}",
                    )
                    chunks: List[str] = []
                    for tok in model.stream(prompt_tokens):
                        chunks.append(tok)
                        self.clock.advance(5)  # fake decode tick
                    reply = "".join(chunks).rstrip()
                    if is_primary:
                        self.breaker.record_success()
                    assistant = Turn(
                        message_id=message_id,
                        role="assistant",
                        content=reply,
                        model=model.name,
                        correlation_id=correlation_id,
                        status="completed",
                    )
                    stored = self.store.append(assistant)
                    self._log(
                        correlation_id,
                        f"completed model={model.name} tokens_out={len(chunks)}",
                    )
                    return stored
                except TransientProviderError as exc:
                    last_err = exc
                    if is_primary:
                        self.breaker.record_failure()
                    jitter = det_jitter_ms(correlation_id, attempt)
                    backoff = self.base_backoff_ms * (2**attempt) + jitter
                    self._log(
                        correlation_id,
                        f"retry model={model.name} err={exc} backoff_ms={backoff}",
                    )
                    self.clock.advance(backoff)

            self._log(correlation_id, f"exhausted_retries model={model.name}")

        # Deterministic template fallback
        self._log(correlation_id, "template_fallback")
        assistant = Turn(
            message_id=message_id,
            role="assistant",
            content="I'm temporarily unavailable. Please try again.",
            model="template",
            correlation_id=correlation_id,
            status="completed",
        )
        return self.store.append(assistant)


def demo() -> None:
    clock = DetClock()
    breaker = CircuitBreaker(failure_threshold=2, cool_down_ms=100, clock=clock)
    loop = ChatTurnLoop(
        store=ConversationStore(),
        primary=FakeModel(name="gpt-4o-fake", fail_times=2),
        fallbacks=[FakeModel(name="gpt-mini-fake", fail_times=0)],
        breaker=breaker,
        clock=clock,
    )

    cid = "corr-001"
    t1 = loop.run_user_turn(
        message_id="msg-aaa",
        user_text="Hello",
        correlation_id=cid,
        prompt_tokens=5000,
    )
    # Duplicate submit — must not call models again
    t1b = loop.run_user_turn(
        message_id="msg-aaa",
        user_text="Hello",
        correlation_id="corr-002",
        prompt_tokens=5000,
    )
    assert t1.message_id == t1b.message_id == "msg-aaa"
    assert t1.content == t1b.content
    assert t1.model == "gpt-mini-fake"  # primary failed twice → breaker/fallback

    # After cool-down, half-open probe can use primary again
    clock.advance(100)
    primary2 = FakeModel(name="gpt-4o-fake", fail_times=0)
    loop.primary = primary2
    t2 = loop.run_user_turn(
        message_id="msg-bbb",
        user_text="Second",
        correlation_id="corr-003",
        prompt_tokens=2000,
    )
    assert t2.model == "gpt-4o-fake"
    assert t2.status == "completed"

    print("OK turns:", [(x.role, x.message_id, x.model, x.content) for x in
                        [loop.store.turns[i] for i in loop.store.order]])
    print("OK log lines:", len(loop.log))
    for line in loop.log:
        print(line)


if __name__ == "__main__":
    demo()
```

**What this demonstrates**

| Concern | Mechanism in code |
| --- | --- |
| Idempotency | `ConversationStore.append` keyed by client `message_id` |
| Fake stream | `FakeModel.stream` yields deterministic tokens |
| Retries + jitter | `base_backoff_ms * 2**attempt + det_jitter_ms(...)` |
| Circuit breaker | `CLOSED → OPEN → HALF_OPEN` with cool-down on `DetClock` |
| Fallback chain | primary → fallbacks → template string |
| Correlation IDs | every log line prefixed `cid=...` |

---

## Part 6 — Architectural System Design Scenarios

### Scenario A — Multi-device personal productivity assistant

**Problem statement**  
Design a single-tenant personal assistant for ~50k MAU: multi-turn streaming chat, light preference memory, optional calendar/email tools, and seamless phone↔laptop handoff. Users abandon if mid-answer is lost when switching devices. Budget target ≈ research toy scale evolving to thousands of conversations/day; p50 perceived start-of-answer under ~500 ms TTFT heuristic ([#144](https://newsletter.systemdesign.one/p/ai-chat-assistant); [Ably](https://ably.com/blog/ai-session-continuity-cross-device-channel-based-architecture)).

**Proposed architecture (component diagram)**

```
  ┌─────────┐   ┌─────────┐
  │ Mobile  │   │  Web    │
  └────┬────┘   └────┬────┘
       │ OIDC        │
       ▼             ▼
  ┌────────────────────────────────────────┐
  │         API GATEWAY + QUOTAS           │
  └───────────────┬────────────────────────┘
                  │
       ┌──────────┼──────────┐
       ▼          ▼          ▼
  ┌─────────┐ ┌────────┐ ┌────────────────┐
  │ Conv.   │ │Context │ │ Stream + PubSub│
  │ Service │ │Builder │ │ (SSE + channel)│
  │(Postgres│ │sys+K+  │ │ sequence resume│
  │ SoR)    │ │memory) │ └────────┬───────┘
  └────┬────┘ └───┬────┘          │
       │          ▼               │
       │    ┌───────────┐         │
       │    │ Model API │         │
       │    │ stream=1  │─────────┘
       │    └─────┬─────┘
       │          ▼
       │    ┌───────────┐
       └───►│ MCP tools │── vault ──► Calendar / Email
            │ RBAC+HITL │
            └───────────┘
```

**Trade-off matrix**

| Approach | Cost | Latency | Ops | Security | Scalability |
| --- | --- | --- | --- | --- | --- |
| **A1. Plain SSE only** (no channel) | Low eng $ | Good on one device | Low | Medium | Poor multi-device (no mid-stream resume) |
| **A2. Server-canonical + pub/sub channel** (recommended) | Medium | Excellent resume; TTFT if unbuffered | Medium | Strong (you own ACLs) | Scales with channel + DB; devices catch up by sequence ([Ably](https://ably.com/blog/ai-session-continuity-cross-device-channel-based-architecture)) |
| **A3. Provider Conversations as sole SoR** | Medium API $ | Excellent | Lower state code | Medium (vendor retention / residency) | Easy cross-device; compliance coupling |

**Decision rationale**  
Choose **A2**. Multi-device handoff is a hard product requirement; plain SSE (A1) cannot resume mid-stream on a new device. Provider Conversations (A3) simplify sync but couple retention/compliance and make GDPR deletes / export harder. Own the Postgres transcript + channel fan-out; use provider APIs for generation (and optionally Conversations as a secondary cache), with prompt caching on the stable system+tool prefix and `strict` MCP tools behind a vault.

---

### Scenario B — Multi-tenant enterprise support copilot

**Problem statement**  
Design a multi-tenant customer-support copilot: SSO, per-tenant isolation, RAG over policies/tickets, tools (refund, CRM update), full audit, cost control at **thousands of conversations/day** ([#144](https://newsletter.systemdesign.one/p/ai-chat-assistant)). Agents switch desks/devices; money-moving tools need HITL. Need router economics so FAQ triage does not burn frontier prices (~$26.50 / 1k frontier turns uncached GPT-4o shape from Part 3).

**Proposed architecture (component diagram)**

```
  ┌──────────────┐
  │ Agent UI     │
  └──────┬───────┘
         │ SSO / SAML
         ▼
  ┌──────────────────────────────────────────────┐
  │ CONTROL: tenant router · FGA · audit gateway │
  └──────┬───────────────┬───────────────┬───────┘
         ▼               ▼               ▼
  ┌────────────┐  ┌────────────┐  ┌─────────────────┐
  │ RAG + ACL  │  │ Model      │  │ Tool bus (MCP)  │
  │ (policies, │  │ router     │  │ refund · CRM    │
  │  tickets)  │  │ mini|mid|  │  │ HITL gate       │
  └─────┬──────┘  │ frontier   │  └────────┬────────┘
        │         └─────┬──────┘           │
        │               │                  │
        └──────────────►│◄─────────────────┘
                        ▼
              ┌──────────────────┐
              │ Stream + Conv DB │── immutable turn log
              │ (tenant_id key)  │── PII redact before sinks
              └──────────────────┘
```

**Trade-off matrix**

| Approach | Cost | Latency | Ops | Security | Scalability |
| --- | --- | --- | --- | --- | --- |
| **B1. Single frontier model, no RAG** | Highest $/turn | Good TTFT if short prompts | Low | Weak (stale policy in weights; broad tool blast radius) | TPM becomes the ceiling fast |
| **B2. Router + RAG/FGA + tool bus + HITL** (recommended) | Controlled (mini triage, frontier hard cases; cache prefix) | Extra retrieve hop; TTFT still stream-bound | Medium–high | Strong isolation, audit, least-privilege tools ([OWASP](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html)) | Horizontal context workers; TPM planned pre-cache ([Community](https://community.openai.com/t/does-prompt-caching-reduce-tpm/1138631)) |
| **B3. Fully self-hosted vLLM** | CapEx/OpEx heavy | Controllable | High | Strongest data plane | Worth it when steady tokens + data boundary beat API economics ([#144](https://newsletter.systemdesign.one/p/ai-chat-assistant)) |

**Decision rationale**  
Choose **B2** for the default enterprise path. Live policies cannot live in training data — RAG with ACL/FGA is mandatory ([#144](https://newsletter.systemdesign.one/p/ai-chat-assistant)). Router economics beat B1 at thousands of conversations/day. Self-host (B3) only when compliance boundary or sustained TPM makes API unit economics lose. Enforce Zero-Trust MCP, immutable turn logs, PII detect→redact→audit before analytics persistence, and delay refund side effects until output guards pass. Remember: **prompt-cache savings do not raise TPM headroom**.

---

### Interview prompts (after both scenarios)

1. Walk the request path when a user sends a message on mobile mid-stream and opens the laptop — what is durable vs connection-scoped?  
2. Compute `$ / 1k turns` for 5k/1.4k on GPT-4o with and without cached input; what does caching *not* buy you on rate limits?  
3. Draw the circuit-breaker state machine and place it relative to a frontier→mini→template fallback chain.  
4. Why is client `message_id` idempotency required on flaky mobile networks?  
5. Compare long context vs compaction vs memory retrieval for a 6-month support thread.  
6. Where do you put the input/output guard boundary when tools can move money?  
7. Design Zero-Trust MCP for calendar send-email: authz checks, schema validation, credential custody, HITL.  
8. Providers publish no TTFT p99 SLA — what do you put in your SLO doc and how do you mitigate p99?  
9. Multi-tenant copilot: how do FGA filters on RAG prevent cross-tenant prompt injection via retrieved tickets?  
10. SSE buffer at a reverse proxy improves throughput metrics but users complain — diagnose via TTFT telemetry.

---
## Sources (selected)

- [System Design Newsletter #144](https://newsletter.systemdesign.one/p/ai-chat-assistant)  
- [OpenAI Streaming](https://developers.openai.com/api/docs/guides/streaming-responses) · [Conversation state](https://developers.openai.com/api/docs/guides/conversation-state) · [Compaction](https://developers.openai.com/api/docs/guides/compaction) · [Prompt Caching](https://developers.openai.com/api/docs/guides/prompt-caching) · [Rate limits](https://developers.openai.com/api/docs/guides/rate-limits) · [GPT-4o pricing](https://developers.openai.com/api/docs/models/gpt-4o)  
- [Anthropic Streaming / TTFT](https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/reduce-latency)  
- [Ably — AI session continuity](https://ably.com/blog/ai-session-continuity-cross-device-channel-based-architecture)  
- [OWASP Prompt Injection Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html) · [OWASP LLM Top 10](https://genai.owasp.org/)  
- Full source list: `research/17-personal-ai-chat-assistant.md`
