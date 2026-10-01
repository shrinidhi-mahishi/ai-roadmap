# Research: Design Personal AI Chat Assistant

**Date researched**: 2026-09-30
**Sources consulted**: 28
**Scope note**: Product-level system design that *composes* streaming chat APIs, conversation memory, optional tool use, safety, and multi-device sync into one assistant. RAG, agent memory engines, MCP/tool protocols, and guardrail stacks are cited briefly via primary sources — not restated as full topics. The System Design Newsletter #144 teaser is the framing article; quantitative backbone comes from OpenAI / Anthropic / OWASP / realtime-sync primaries.

> ⚠️ Primary article ([System Design Newsletter #144](https://newsletter.systemdesign.one/p/ai-chat-assistant)) is partially paywalled (May 04, 2026). Free/teaser sections supply architecture requirements and tokenization/post-training framing; paywalled Part 5 build code and some worked cost tables are noted where inferred from official APIs instead.

## 1. System Topology & Mechanics

### Why build vs wrap a consumer chat product

Customer-facing assistants need product knowledge, policies, and per-user history that live in *your* systems — often too large for a context window, too sensitive for unconstrained third-party logging, and too dynamic for a static prompt ([System Design Newsletter #144](https://newsletter.systemdesign.one/p/ai-chat-assistant)). Building your own buys: privacy/data-control, system-prompt ownership, cost levers (caching, routing, compaction), product-embedded auth/UI/context, and a controlled request path for injection defenses and output policies ([#144](https://newsletter.systemdesign.one/p/ai-chat-assistant)).

Newsletter design target for the *pure conversational* build: multi-turn context, SSE streaming, configurable decoding (creative vs precise), prompt-injection resistance, cost control at **thousands of conversations/day**, plus optional persistent memory across sessions. Explicit non-goals for that base build: no document retrieval, no domain expertise beyond training data, no tool/actions — add those layers when the product requires them ([#144](https://newsletter.systemdesign.one/p/ai-chat-assistant)).

### Four-layer request path (composition spine)

Teaser architecture: user message → **context engineering** → **generation engine** → **persistent memory** → **SSE streaming** → client ([#144](https://newsletter.systemdesign.one/p/ai-chat-assistant)). In a production personal/enterprise assistant, those layers expand to:

| Plane | Responsibility | Typical components |
| --- | --- | --- |
| Control / product | AuthN/Z, conversation CRUD, policy, routing | API gateway, SSO/OIDC, conversation store, model router |
| Context assembly | System prompt, history window, memory/RAG snippets, tool schemas | Prompt builder, compaction, optional retriever |
| Generation | Prefill + decode; stream tokens / tool args | Provider API or self-hosted (vLLM / SGLang / llama.cpp when outgrowing APIs) ([#144](https://newsletter.systemdesign.one/p/ai-chat-assistant)) |
| Delivery + sync | Token fan-out, reconnect, multi-device | SSE/WebSocket gateway, pub/sub channel, presence |

Inference itself is still next-token prediction over tokens. Prefill processes the full prompt; decode emits tokens autoregressively using a KV-cache of prior keys/values — longer history raises both latency and cost because prefill work and billed input tokens grow with every turn ([#144](https://newsletter.systemdesign.one/p/ai-chat-assistant); [OpenAI Prompt Caching](https://developers.openai.com/api/docs/guides/prompt-caching) — KV states are what caches reuse).

### Tokenization consequences for chat UX

Models operate on subword tokens (commonly BPE), not characters/words. A typical English sentence of **10 words ≈ 13–15 tokens**; code and many non-English languages are denser ([#144](https://newsletter.systemdesign.one/p/ai-chat-assistant)). Character-level tasks (e.g. counting letters in “strawberry”) fail when letters split across token boundaries ([#144](https://newsletter.systemdesign.one/p/ai-chat-assistant)). Pricing, context limits, and TTFT all scale with token count, not word count.

Post-training (SFT then RLHF/preference optimization) is what makes a base LM behave as a chat assistant that follows system prompts and refuses some harmful asks ([#144](https://newsletter.systemdesign.one/p/ai-chat-assistant)). Scaling-law tiering (frontier vs mini/nano) is the rationale for model routing later in §2.

### Streaming transport (data plane)

**OpenAI — Responses API (recommended for new streaming):** `stream=true` over SSE; typed semantic events such as `response.created`, repeated `response.output_text.delta`, `response.completed`, `error`; tool args stream via `response.function_call_arguments.delta` / `.done` ([OpenAI — Streaming](https://developers.openai.com/api/docs/guides/streaming-responses)). WebSocket mode exists for persistent transport with incremental inputs via `previous_response_id` ([same](https://developers.openai.com/api/docs/guides/streaming-responses)).

**OpenAI — Chat Completions (still supported):** `stream=true` yields `data:` JSON chunks with `choices[].delta.content`, terminated by `data: [DONE]` ([OpenAI — Streaming](https://developers.openai.com/api/docs/guides/streaming-responses)). Migration note: Chat Completions requires the app to resend the full `messages` array; Responses adds `previous_response_id` and Conversations for server-side state ([OpenAI — Migrate to Responses](https://developers.openai.com/api/docs/guides/migrate-to-responses); [Conversation state](https://developers.openai.com/api/docs/guides/conversation-state)).

**Anthropic — Messages API:** SSE typed events in order: `message_start` → (`content_block_start` → `content_block_delta`* → `content_block_stop`)* → `message_delta` → `message_stop` ([Anthropic — Streaming](https://platform.claude.com/docs/en/build-with-claude/streaming)). Text TTFT should be measured on first `content_block_delta` with `delta.type == text_delta`, not on `message_start` ([Anthropic — Reducing latency](https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/reduce-latency)). Tool-use blocks stream `input_json_delta` / `partial_json`; fine-grained streaming via per-tool `eager_input_streaming: true` disables server-side JSON buffering/validation so clients must accumulate and repair ([Anthropic — Fine-grained tool streaming](https://platform.claude.com/docs/en/agents-and-tools/tool-use/fine-grained-tool-streaming)).

**Moderation vs streaming:** OpenAI explicitly warns that streaming makes content moderation harder because partial completions are harder to evaluate; if moderation scores are requested with generation, they arrive after the full output — not with partial deltas ([OpenAI — Streaming](https://developers.openai.com/api/docs/guides/streaming-responses); [Moderation](https://developers.openai.com/api/docs/guides/moderation)).

### Conversation memory (short-term / session)

Three OpenAI-documented patterns ([Conversation state](https://developers.openai.com/api/docs/guides/conversation-state)):

1. **Manual transcript** — app persists turns and resends `messages` / `input` each call (Chat Completions default mental model).
2. **`previous_response_id` chaining** — pass only the new user turn; provider threads prior response context. Response objects retained **30 days** by default; set `store: false` to disable. **Billing note:** prior input tokens in the chain are still billed as input ([Conversation state](https://developers.openai.com/api/docs/guides/conversation-state)).
3. **Conversations API** — durable `conversation_id` holding messages, tool calls, and tool outputs; usable “across sessions, devices, or jobs”; conversation items are **not** subject to the 30-day response TTL ([Conversation state](https://developers.openai.com/api/docs/guides/conversation-state)).

Long-running windows: server-side **compaction** via `context_management` / `compact_threshold` (example threshold **200,000** tokens) emits an opaque compaction item and prunes context mid-stream; standalone `POST /responses/compact` returns a new compacted window for ZDR-friendly flows with `store=false` ([OpenAI — Compaction](https://developers.openai.com/api/docs/guides/compaction)).

Realtime voice/chat sessions: **32,768**-token window, max **4,096** output → ~**28,672** max input before truncation; session instructions+tools capped at **16,384** tokens; sessions up to **60 minutes**; truncation drops oldest messages and busts prompt-cache prefixes unless `retention_ratio` (e.g. **0.8**) truncates in larger chunks ([OpenAI Realtime notes](https://developers.openai.com/blog/realtime-api); [Realtime Create session](https://developers.openai.com/api/reference/resources/realtime/subresources/sessions/methods/create/)).

Cross-session “personal memory” (preferences, facts) is a separate store from the turn transcript — typically extracted summaries / structured user profile written after turns and re-injected into context engineering ([#144](https://newsletter.systemdesign.one/p/ai-chat-assistant)). When product knowledge must be retrieved, compose with RAG (vector/index retrieve → cite → generate) rather than stuffing corpora into the system prompt ([inferred] product composition; RAG mechanics covered in topic 06).

### Tool use (optional agentic layer)

Pure newsletter build omits tools; enterprise assistants usually add them. OpenAI function tools: JSON-schema `parameters`, recommended `strict: true` (Structured Outputs–backed schema adherence); `tool_choice` ∈ {`auto`, `required`, named function, `none`, allowed-tools subset}; `parallel_tool_calls` can be set `false` to force 0–1 call ([OpenAI — Function calling](https://platform.openai.com/docs/guides/function-calling)). Large tool catalogs can defer via tool search on supported models (`gpt-5.4`+) ([same](https://platform.openai.com/docs/guides/function-calling)). Host credentials and state-changing side effects in application code with deterministic policy checks — not in the model (OWASP / NIST framing in §4).

### Multi-device sync topology

HTTP SSE is connection-scoped: a new device has no stream cursor and cannot resume mid-response ([Ably — AI session continuity](https://ably.com/blog/ai-session-continuity-cross-device-channel-based-architecture)). Production pattern: **server-canonical conversation** + **channel/pub-sub** for live tokens; reconnect = history catch-up then live subscribe; presence tracks which devices are active ([Ably](https://ably.com/blog/ai-session-continuity-cross-device-channel-based-architecture)). Classic chat sync: each device keeps `cur_max_message_id` (or equivalent sequence) and pulls newer messages from the store ([ByteByteGo — Design a chat system](https://bytebytego.com/courses/system-design-interview/design-a-chat-system)). Provider-side, OpenAI Conversations are explicitly positioned for cross-device continuity ([Conversation state](https://developers.openai.com/api/docs/guides/conversation-state)).

### Control-plane vs data-plane (inference routing)

At provider scale, OpenAI describes an inference load balancer with a **synchronous local data plane** (per-request routing from cached engine snapshots) and an **asynchronous control plane** (optimizes routing weights from volume, network latency, capacity, TTFT/TBOT profiles, KV-cache locality) ([OpenAI ILB talk summary](https://finance.biggo.com/podcast/afa4b13a445a4639)). App builders mirror a lighter version: edge gateway (auth, rate limit, moderation) → context service → model router → stream gateway.

## 2. Token Economics & NFR Metrics

### Worked token math (illustrative, not a fabricated SLA)

Assume a support-style turn: **5,000** input tokens (system + tools + history) and **1,400** output tokens, **500** conversations/day ≈ **15,000** turns/month if 1 turn/conversation average — scale turns upward for multi-turn chats ([inferred] from common chatbot workload shapes; align with [#144](https://newsletter.systemdesign.one/p/ai-chat-assistant) “thousands of conversations/day”).

| Model (published list prices) | Input $/1M | Cached input $/1M | Output $/1M | Cost / turn (uncached) | Cost / turn (100% input cached) |
| --- | ---: | ---: | ---: | ---: | ---: |
| GPT-4o | $2.50 | $1.25 | $10.00 | 5k×$2.50e-6 + 1.4k×$10e-6 = **$0.0265** | 5k×$1.25e-6 + 1.4k×$10e-6 = **$0.02025** |
| GPT-4.1 | $2.00 | $0.50 | $8.00 | **$0.0212** | **$0.0137** |
| Claude Sonnet 4.5 | $3.00 | $0.30 (cache hit) | $15.00 | **$0.0360** | **$0.0225** |

Sources: [OpenAI GPT-4o model](https://developers.openai.com/api/docs/models/gpt-4o), [GPT-4.1 model](https://developers.openai.com/api/docs/models/gpt-4.1), [Anthropic Prompt Caching pricing table](https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching). At **15,000** turns/month on GPT-4o uncached ≈ **$398**/month for that toy workload; with strong cache hits, input line drops ~**50%** on GPT-4o cached rate ([inferred] arithmetic from table).

Anthropic cache economics: default **5-minute** ephemeral TTL (refreshed on hit); optional **1-hour** TTL; **5m write = 1.25×** base input, **1h write = 2×**, **cache read ≈ 0.1×** base (some models **0.05× / 0.025×**) ([Anthropic Prompt Caching](https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching)). Automatic caching moves the breakpoint forward each turn so growing history reuses the stable prefix ([same](https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching)).

OpenAI prompt caching: enabled by default for supported models; discounts on cached input **up to 95%** depending on model; for **GPT-5.6+**, cache writes **1.25×** uncached input and reads **0.1×** ( **0.05×** on GPT-6.1 Sol); minimum cacheable prefix length **1,024** tokens (GPT-5.6+) ([OpenAI Prompt Caching](https://developers.openai.com/api/docs/guides/prompt-caching)). Prefix must match exactly — tools, structured-output schemas, reasoning effort, verbosity, and compaction all affect the cached prefix ([same](https://developers.openai.com/api/docs/guides/prompt-caching)). Cookbook guidance: ~**15 RPM** per `(prefix + prompt_cache_key)` before load-balancing spreads traffic and causes one-time misses ([Prompt Caching 201](https://developers.openai.com/cookbook/examples/prompt_caching_201)). **Cache hits do not reduce TPM rate-limit accounting** — TPM is enforced before cache lookup ([OpenAI Developer Community](https://community.openai.com/t/does-prompt-caching-reduce-tpm/1138631); [Rate limits](https://developers.openai.com/api/docs/guides/rate-limits)).

### Latency NFRs

| Metric | Guidance | Source quality |
| --- | --- | --- |
| TTFT (time to first **text** token) | Primary UX metric for streaming; measure client-side on first non-empty text delta | [Anthropic Reducing latency](https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/reduce-latency); [OpenAI Streaming](https://developers.openai.com/api/docs/guides/streaming-responses) |
| Perceived “instant” band | ~**<500 ms** TTFT often cited as psychological threshold for streaming UIs | Secondary ([Neural Base / industry writeups](https://theneuralbase.com/ai-apis-comparison/learn/intermediate/time-to-first-token-by-provider/)) — treat as design heuristic, not OpenAI SLA |
| Prefill scaling | TTFT grows with input length (and uncached prefix); KV-cache / prompt cache cuts repeated prefill | [#144](https://newsletter.systemdesign.one/p/ai-chat-assistant); [OpenAI Prompt Caching](https://developers.openai.com/api/docs/guides/prompt-caching) |
| p99 TTFT | Dominated by queueing, routing, KV allocation — not raw decode FLOPs | [DigitalOcean — Debugging p99 TTFT](https://www.digitalocean.com/community/tutorials/debugging-p99-ttft-llm-inference) |

> ⚠️ Limited public data available for this dimension (provider TTFT SLAs). OpenAI / Anthropic do not publish contractual p50/p95/p99 TTFT for shared chat APIs. Secondary aggregates (e.g. GPT-4o ~200–400 ms US-East on dedicated capacity) are **not** treated as benchmarks here.

### Throughput & back-pressure

OpenAI rate limits are multi-dimensional (RPM, TPM, RPD, TPD, …); whichever trips first returns `429` with `Retry-After` / `x-ratelimit-*` headers ([Rate limits](https://developers.openai.com/api/docs/guides/rate-limits)). Published tier examples (Astra/Sol/Terra family):

| Tier | RPM | TPM |
| --- | ---: | ---: |
| Build | 5,000 | 1,000,000 |
| Launch | 10,000 | 4,000,000 |
| Grow | 15,000 | 40,000,000 |

([Rate limits](https://developers.openai.com/api/docs/guides/rate-limits)). Ramp rule of thumb once at ~**1M input TPM**: increase by no more than **50%** every **15 minutes** ([same](https://developers.openai.com/api/docs/guides/rate-limits)). Spend alerts vs hard spend limits (`429` at cap) are separate controls ([same](https://developers.openai.com/api/docs/guides/rate-limits)).

### Dynamic model routing

[#144](https://newsletter.systemdesign.one/p/ai-chat-assistant) and scaling-law framing: route simple/FAQ turns to mini/nano; hard reasoning / tool-heavy turns to frontier. [inferred] Implement as: classifier or heuristic on query length/tool need/user tier → model id; keep system prompt stable across tiers where possible to preserve cache prefixes.

## 3. Distributed Resilience & State

### Durable conversation state

| Store | Semantics | Failure / retention notes |
| --- | --- | --- |
| App DB (Postgres/etc.) | Canonical transcript, branches, `active_leaf_id` | Required for multi-device and compliance deletes; tree > flat list for regenerate/edit ([AI Systems — Design ChatGPT](https://ikshitij.com/learn/ai-systems/chatgpt-chat-app/) — secondary but widely used design pattern) |
| OpenAI Conversations | Provider-durable items, cross-device | No 30-day TTL on conversation items ([Conversation state](https://developers.openai.com/api/docs/guides/conversation-state)) |
| `previous_response_id` | Soft chain | **30-day** response retention; broken ID → resend full context ([Conversation state](https://developers.openai.com/api/docs/guides/conversation-state)) |
| Redis / hot cache | Last N turns + summary | Fast resume; not source of truth |
| Pub/sub channel | Live token fan-out + history replay | Reconnect catch-up ([Ably](https://ably.com/blog/ai-session-continuity-cross-device-channel-based-architecture)) |

Write path [inferred] for production assistants: authenticate → rate-limit → append user message (durable) → assemble context → call model with streaming → persist assistant tokens (or final message) → fan-out to devices. Prefer append-only message logs for auditability ([handbook case-study pattern](https://github.com/handbook-academy/engineering-handbook/blob/main/content/hld/part-8-case-studies/30-chatgpt-conversational-ai.md) — secondary).

### Checkpointing & compaction

- **Turn checkpoint:** persist user message before inference; persist assistant message on `response.completed` / `message_stop` so a crashed stream can show partial + “interrupted” rather than losing the user turn ([inferred] from streaming lifecycle events).
- **Context checkpoint:** OpenAI compaction items are opaque encrypted carriers of prior state — pass through, don’t invent human-readable summaries as a substitute unless you own summarization ([Compaction](https://developers.openai.com/api/docs/guides/compaction)).
- **Realtime truncation:** `truncation: "disabled"` returns error at limit; `retention_ratio` amortizes cache busts ([Realtime](https://developers.openai.com/blog/realtime-api)).

### Concurrency & multi-device locking

Optimistic concurrency on conversation revision / `active_leaf_id` avoids two devices forking silent conflicts ([inferred] from tree-branch model). Presence: if all devices disconnect mid-stream, decide whether to cancel generation (save cost) or finish and push notification ([Ably](https://ably.com/blog/ai-session-continuity-cross-device-channel-based-architecture)).

### Circuit breakers & degradation

| Layer | Pattern |
| --- | --- |
| Provider 429/503 | Honor `Retry-After`; exponential backoff; shed to smaller model or cached FAQ ([Rate limits](https://developers.openai.com/api/docs/guides/rate-limits)) |
| Tool backends | Bulkheads + timeouts separate from LLM timeout budget ([inferred]) |
| Streaming proxy | Must flush SSE — buffering destroys TTFT ([DigitalOcean p99 TTFT](https://www.digitalocean.com/community/tutorials/debugging-p99-ttft-llm-inference)) |
| Memory/RAG | Fail open to “no retrieved context” with banner rather than blocking chat ([inferred]) |

### Rate-limit fallbacks

Token-bucket / sliding-window per `(user_id, tier)`; batch when RPM-bound but TPM-rich ([Rate limits](https://developers.openai.com/api/docs/guides/rate-limits)). Graceful chain: frontier → mid → mini → template refusal.

## 4. Enterprise Security & Governance

### Threat model for chat assistants

OWASP LLM Top 10 (2025/2026): **LLM01 Prompt Injection** is acute once tools, RAG, or long-term memory exist — injected instructions in user text *or* retrieved content can exfiltrate data or drive tools ([OWASP LLM Top 10](https://genai.owasp.org/); [OWASP Prompt Injection Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html)). Memory poisoning taints future sessions; agentic tool use expands blast radius ([OWASP 2026 materials](https://genai.owasp.org/)).

### Defense-in-depth (compose, don’t rely on system prompt alone)

| Layer | Mechanism | Primary refs |
| --- | --- | --- |
| Input/output classifiers | OpenAI `omni-moderation-latest` (free; text+image ≤20 MB); optional Llama Guard / NeMo rails | [OpenAI Moderation](https://developers.openai.com/api/docs/guides/moderation); [NeMo Guardrails](https://github.com/NVIDIA/NeMo-Guardrails) |
| Privilege separation | Dual-LLM / quarantined reader vs privileged actor; Rule of Two for (untrusted input × sensitive data × state change) | [OWASP Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html); Meta Rule of Two cited in OWASP 2026 |
| Tool RBAC | Allowlisted tools, `strict` schemas, human-in-the-loop for irreversible actions; FGA over RAG corpora and tools | [Function calling](https://platform.openai.com/docs/guides/function-calling); Auth0 FGA-for-RAG framing in [#144](https://newsletter.systemdesign.one/p/ai-chat-assistant) partner section |
| Credentials | Token vault / app-held secrets — never model-held API keys | [#144](https://newsletter.systemdesign.one/p/ai-chat-assistant) |
| Streaming safety | Buffer-and-check high-risk sessions, or moderate completed output before committing side effects; OpenAI notes streaming moderation difficulty | [Streaming](https://developers.openai.com/api/docs/guides/streaming-responses) |

### Auth, tenancy, compliance

Enterprise assistants need SSO/OIDC/SAML, SCIM, and per-user conversation isolation from day one for chat history and personalization ([#144](https://newsletter.systemdesign.one/p/ai-chat-assistant)). ChatGPT Business/Enterprise advertise encryption in transit (TLS 1.2) and at rest (AES-256), SOC 2 Type 2, and Enterprise extras (SCIM, EKM, RBAC, data residency in multiple regions, no training on business data by default) ([OpenAI Business pricing/trust](https://openai.com/api/pricing/)) — use as a **product checklist** when designing your own control plane, not as a claim that your build inherits those attestations.

PII: redact or tokenize before logs/analytics; retention ≠ model training consent. GDPR/HIPAA imply deletable conversation stores and BAA-capable providers where applicable ([inferred] regulatory requirements; confirm with counsel).

### Audit logs

Log: `user_id`, `conversation_id`, `message_id`, model, token usage, tool name+args hash, moderation flags, policy decisions, `request_id` / provider ids. Prefer append-only immutable storage for SOC2 evidence ([inferred] common compliance practice).

## 5. Production Failure Modes

| Failure | Symptoms | Detection | Mitigation |
| --- | --- | --- | --- |
| Context window degradation | Truncation, lost early constraints, “forgets” system rules | Token counters; rising compaction frequency | Sliding window + summary; OpenAI compaction; Realtime `retention_ratio`; RAG for facts instead of infinite history ([Compaction](https://developers.openai.com/api/docs/guides/compaction); [Realtime](https://developers.openai.com/blog/realtime-api)) |
| Stream disconnect / partial reply | Client shows incomplete answer; other devices never see tokens | Missing `response.completed` / `message_stop`; channel sequence gaps | Persist partials; replay from sequence; idempotent message ids ([Ably](https://ably.com/blog/ai-session-continuity-cross-device-channel-based-architecture)) |
| Prompt injection / jailbreak | Policy bypass, tool misuse, prompt leak | Moderation + canary instructions + tool-policy denials | Least-privilege tools; dual-LLM; HITL ([OWASP Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html)) |
| Hallucinated tool parameters | Invalid JSON / wrong types / invented ids | Schema validation (`strict`); Anthropic parse-on-`content_block_stop` | Reject + retry-with-correction; disable fine-grained streaming if validity > latency ([Function calling](https://platform.openai.com/docs/guides/function-calling); [Fine-grained tool streaming](https://platform.claude.com/docs/en/agents-and-tools/tool-use/fine-grained-tool-streaming)) |
| Infinite / long tool loops | Runaway cost, oscillating tools | Max iterations, $ cap, duplicate-call detector | Hard stop; `parallel_tool_calls: false` when needed ([Function calling](https://platform.openai.com/docs/guides/function-calling)) |
| Cache stampede / miss storms | Cost spike, TTFT spike | Cache hit-rate dashboards | Stable prefix ordering; `prompt_cache_key` granularity ≤~15 RPM ([Prompt Caching](https://developers.openai.com/api/docs/guides/prompt-caching); [Cookbook 201](https://developers.openai.com/cookbook/examples/prompt_caching_201)) |
| TPM exhaustion despite cache hits | Unexpected `429` | Rate-limit headers | Caching ≠ TPM relief ([Community](https://community.openai.com/t/does-prompt-caching-reduce-tpm/1138631)) |
| Cascading timeouts | Gateway waits on LLM waits on tool | Deadline propagation, bulkheads | Separate budgets: e.g. tools 2–5 s, LLM stream idle timeout, client UX cancel ([inferred]) |
| State drift (multi-device) | Forked branches, duplicate assistants | Version / leaf mismatch | Single-writer election per conversation or CRDT/merge UI ([inferred]) |
| Output moderation gap on stream | Harmful tokens already rendered | Post-hoc flags after completion | Delay side effects until full moderation; optional token-window scrubber ([Streaming moderation note](https://developers.openai.com/api/docs/guides/streaming-responses)) |

> ⚠️ Limited public data available for this dimension (vendor post-mortems). Consumer ChatGPT outage reports rarely publish root-cause token/stream metrics usable as capacity formulas.

## 6. Enterprise System Design Scenarios

### Scenario A — Personal productivity assistant (single tenant / consumer)

**Requirements:** multi-turn chat, streaming, light memory (preferences), optional calendar/email tools, multi-device sync.

**Reference composition:**

1. Mobile/web clients → API gateway (OIDC).
2. Conversation service (Postgres) + Realtime channel for token fan-out.
3. Context builder: system prompt + last K turns + memory profile (+ optional RAG over user files).
4. OpenAI Responses or Anthropic Messages with `stream=true`; tools with `strict` schemas.
5. Moderation on input; output moderation before tool side effects.
6. Prompt caching on stable system+tool prefix.

**Trade-offs:** fastest UX vs hardest streaming safety; provider Conversations simplify sync but couple retention/compliance to vendor.

### Scenario B — Enterprise customer-support copilot (multi-tenant)

**Requirements:** SSO, per-tenant data isolation, RAG over policies/tickets, tool actions (refund, CRM), audit, cost at thousands of conversations/day ([#144](https://newsletter.systemdesign.one/p/ai-chat-assistant)).

**Component choices:**

| Concern | Choice | Why |
| --- | --- | --- |
| Knowledge | RAG with ACL/FGA filters | Training data won’t hold live policies ([#144](https://newsletter.systemdesign.one/p/ai-chat-assistant)) |
| Model | Router: Haiku/mini for triage, Sonnet/GPT-4.1 for complex | Cost × quality ([#144](https://newsletter.systemdesign.one/p/ai-chat-assistant); pricing §2) |
| Actions | Tool bus + HITL for money movement | OWASP least privilege |
| Sync | Server-canonical + channel resume | Device switch without losing answer ([Ably](https://ably.com/blog/ai-session-continuity-cross-device-channel-based-architecture)) |
| Eval | Golden sets + LLM-as-judge | Newsletter quality loop ([#144](https://newsletter.systemdesign.one/p/ai-chat-assistant)) |

### Scenario C — Regulated (healthcare/legal/finance)

Keep conversations on controlled infrastructure; prefer VPC/Bedrock/Azure OpenAI or self-host; disable provider `store` where ZDR needed; compaction with `store=false` ([Compaction](https://developers.openai.com/api/docs/guides/compaction)); PII redaction before any third-party log; immutable audit. Off-the-shelf ChatGPT UI is usually insufficient ([#144](https://newsletter.systemdesign.one/p/ai-chat-assistant)).

### Capacity planning sketch

> ⚠️ Limited public data available for this dimension (end-to-end hyperscaler chat capacity). Use API quotas + measured TTFT/TPM, not scraped “ChatGPT QPS” rumors.

[inferred] Example: **2,000** conversations/day × **6** turns × **4,000** input + **800** output tokens ≈ **48M** input + **9.6M** output tokens/day. On GPT-4.1 list prices ≈ **$48×2 + $9.6×8 = $172.8/day** uncached (~**$5.2k/month**); at **70%** input cache hit with $0.50/1M cached: input ≈ 0.3×48×$2 + 0.7×48×$0.50 = **$28.8 + $16.8 = $45.6/day** input + **$76.8** output ≈ **$122/day**. GPU self-host becomes relevant when steady token throughput and data-boundary needs exceed API economics — serve with vLLM/SGLang as [#144](https://newsletter.systemdesign.one/p/ai-chat-assistant) suggests.

### Trade-off matrix

| Approach | Cost | Latency (perceived) | Ops complexity | Security control | Multi-device |
| --- | --- | --- | --- | --- | --- |
| Wrap ChatGPT / Claude.ai | Low eng | Excellent | Low | Weak (tenant/policy) | Vendor-native |
| API + app transcript + SSE | Medium | Excellent if unbuffered | Medium | Strong | DIY sync |
| API + Conversations / `previous_response_id` | Medium | Excellent | Lower state code | Medium (vendor retention) | Easier |
| Channel-based session layer | Medium–high | Best resume | Higher | Strong if you own ACLs | Best |
| Self-hosted model | CapEx/OpEx heavy | Controllable | High | Strongest data plane | DIY |

## Sources

- [1] https://newsletter.systemdesign.one/p/ai-chat-assistant — System Design Newsletter #144: Design a personal AI chat assistant (Neo Kim / Louis-François Bouchard)
- [2] https://developers.openai.com/api/docs/guides/streaming-responses — OpenAI SSE streaming (Responses + Chat Completions)
- [3] https://developers.openai.com/api/docs/guides/conversation-state — Manual state, `previous_response_id`, Conversations API, 30-day retention
- [4] https://developers.openai.com/api/docs/guides/migrate-to-responses — Chat Completions → Responses migration
- [5] https://developers.openai.com/api/docs/guides/compaction — Server-side and standalone context compaction
- [6] https://developers.openai.com/api/docs/guides/prompt-caching — OpenAI prompt caching (KV prefix, up to 95% discount, GPT-5.6+ multipliers)
- [7] https://developers.openai.com/cookbook/examples/prompt_caching_201 — Cache keys, ~15 RPM/prefix, Realtime retention_ratio
- [8] https://developers.openai.com/api/docs/guides/rate-limits — RPM/TPM tiers, headers, ramp guidance
- [9] https://developers.openai.com/api/docs/guides/moderation — `omni-moderation-latest`, free endpoint, inline moderation
- [10] https://platform.openai.com/docs/guides/function-calling — Tools, `strict`, `tool_choice`, parallel calls
- [11] https://developers.openai.com/api/docs/models/gpt-4o — GPT-4o pricing ($2.50 / $1.25 cached / $10)
- [12] https://developers.openai.com/api/docs/models/gpt-4.1 — GPT-4.1 pricing ($2 / $0.50 cached / $8)
- [13] https://developers.openai.com/blog/realtime-api — Realtime window 32k, truncation, retention_ratio, 60-min sessions
- [14] https://developers.openai.com/api/reference/resources/realtime/subresources/sessions/methods/create/ — Realtime session truncation / cache breakpoint API
- [15] https://platform.claude.com/docs/en/build-with-claude/streaming — Anthropic SSE event model
- [16] https://platform.claude.com/docs/en/agents-and-tools/tool-use/fine-grained-tool-streaming — `eager_input_streaming`, partial_json risks
- [17] https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching — Anthropic 5m/1h TTL and cache pricing table
- [18] https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/reduce-latency — TTFT definition and streaming advice
- [19] https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html — Prompt injection mitigations, dual-LLM, guard models
- [20] https://genai.owasp.org/ — OWASP Top 10 for LLM Applications (2025/2026 program)
- [21] https://ably.com/blog/ai-session-continuity-cross-device-channel-based-architecture — Channel-based multi-device AI session continuity
- [22] https://bytebytego.com/courses/system-design-interview/design-a-chat-system — Multi-device `cur_max_message_id` sync pattern
- [23] https://openai.com/api/pricing/ — ChatGPT Business/Enterprise security & compliance feature matrix
- [24] https://community.openai.com/t/does-prompt-caching-reduce-tpm/1138631 — Cache hits do not reduce TPM
- [25] https://www.digitalocean.com/community/tutorials/debugging-p99-ttft-llm-inference — p99 TTFT root causes (queue, KV, proxy buffering)
- [26] https://finance.biggo.com/podcast/afa4b13a445a4639 — OpenAI inference LB control/data plane + KV-aware routing (talk summary)
- [27] https://ikshitij.com/learn/ai-systems/chatgpt-chat-app/ — Conversation tree / active leaf design pattern (secondary)
- [28] https://github.com/NVIDIA/NeMo-Guardrails — Programmable conversational guardrails toolkit
