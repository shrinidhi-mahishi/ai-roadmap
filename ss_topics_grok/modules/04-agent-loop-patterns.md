# Module 04 — Agent Loop Patterns

**Audience**: personal study and enterprise interview prep.
**Grounded in**: `ss_topics_grok/research/04-agent-loop-patterns.md` (researched 2026-09-23, 94 sources). Vendor list prices, tokenizer tables, and SDK HTTP retry constants live in [`01-python-llm-foundations.md`](01-python-llm-foundations.md). Prefix-stability, cache breakpoints, and the **tool-schema tax** live in [`02-context-engineering.md`](02-context-engineering.md). JSON Schema / `strict` / dispatcher IDs / `is_error` mapping live in [`03-tool-calling.md`](03-tool-calling.md) — **do not recopy those tables**. This module is the **control-plane loop**: who decides the next hop, when the model may think vs act vs replan vs reflect, and which fuse (`max_turns`, `recursion_limit`, `max_budget_usd`, watchdog) actually stops spend.
**Mandatory topics**: ReAct · plan-and-execute · reflection · self-correction · max iteration limits.

The model **does not execute tools or terminate the process**. It emits structured actions (or text); **your** runtime decides whether to dispatch, inject an observation, replan, interrupt a human, or halt. Collapsing `max_turns` (model invocations), `recursion_limit` (Pregel super-steps), and `max_budget_usd` (client-side dollars) into one “iteration cap” is how teams ship infinite spend.

---

## What Is This?

An **agent loop** is a control-plane state machine around a growing transcript. **ReAct** interleaves Thought → Action → Observation until the model emits no tool calls — or a fuse fires. **Plan-and-execute** (Plan-and-Solve, LangGraph P&E, ReWOO, LLMCompiler) emits a plan or DAG **before** workers run; a Joiner/replanner may repair remaining steps, capped separately. **Reflection** (Reflexion across trials; Self-Refine in-context) writes verbal feedback into the next attempt — it is **not** a weight update. **Self-correction** is four non-interchangeable mechanisms (tool-error, constitution, verifier, debate); Huang et al. show **oracle-free** “are you sure?” **drops** accuracy. Production is a **deterministic outer graph** wrapping a model-chosen inner loop, with hop / dollar / wall-clock fuses plus a `(tool, canonical_args)` hash circuit.

## Why It Matters

OpenAI Agents SDK default **`max_turns=10`**. On a cached GPT-6-sol short-context toy that is **[inferred] $85 / 1k runs**; a 25-hop runaway is **[inferred] $187 / 1k** — **~2.2×** the 10-hop row, **~6×** a 1k-ticket/day support mix (**~$31/day** → **~$187/day**). ReWOO: interleaved ReAct **5×** tokens vs batched plan (HotpotQA gpt-3.5 **9,795** vs **1,986**; **$19.59** vs **$3.97 per 1k queries**). LLMCompiler Movie Rec: **6.73×** cheaper and **3.74×** faster than ReAct† at **higher** accuracy. ReAct Table 2: **47%** of HotpotQA failures include **repetitive TAO** — the model will not stop itself. Huang: GPT-4 GSM8K **95.5 → 89.0** after **5** oracle-free calls; you paid **5×** to **lose 6.5 pp**. AlfWorld Reflexion needed **12** trials; WebShop died at **4** with no useful reflections. LangGraph `remaining_steps < 2` returns **HTTP-200-shaped** “Sorry, need more steps…” — a silent fuse, not `GraphRecursionError`.

## Interview traps (fail these, fail the round)

- Treating **“turn”** as portable: OpenAI = one model invocation **including** parallel tools; Claude Agent SDK `max_turns` = **tool-use round trips only** (no default; `max_turns=2` can stop **before** an edit); LangGraph `recursion_limit` = **super-steps** (ReAct ≈ **2** per tool round).
- `{"configurable": {"recursion_limit": 50}}` — `recursion_limit` is a **standalone** invoke key.
- `max_turns=None` / `LoopAgent` without `max_iterations` **and** without `escalate` → unbounded spend.
- Deep Agents **parent** 1000/2000/9999 **does not propagate** to subagents (still reported hitting **25**).
- Claude `max_budget_usd` checked **after** a call → overshoot **one** API call; no `result` text on `error_max_turns`.
- `pause_turn` treated as `end_turn` (Anthropic **server-tool** inner cap — resend assistant content).
- Handoff consumes a **turn** of the same OpenAI budget; guardrail tripwires abort **outside** the counter.
- Mixing REFLECT rewrite of PLAN in the same forward pass that calls Stripe (control vs data plane).
- Self-Refine / Huang loop on math without an oracle; Reflexion **without tests** **hurt** (52% vs 60%).
- Replan **every** hop → you paid for a planner and got ReAct. Cap `max_replans` at **2–3**.
- Observation that injects a **new goal** (email, new vendor) → PlanFlip / LLM06; **freeze** the user objective.
- Hash-circuit missing: greedy ReAct repeats `(tool, args)`; AutoGPT spent **51+ min** / **21+** empty `input: {}`.
- Compaction rewriting the **cached prefix** (02) after hop 8 of 25.
- `trace_include_sensitive_data` default **true**; PostgresSaver is a **PII store**.

---

## 1. System Topology & Data Flow

### 1.1 Topology

Control plane owns the **loop controller** (ReAct vs P&E vs reflection dispatch), **hop counters** (`max_turns` / `recursion_limit` / `remaining_steps` / `max_budget_usd`), **hash circuit**, **checkpointer** `thread_id`, optional **critic** (oracle-gated), HITL interrupt, and which tools are legal **this hop**. It does **not** own transformer weights or KV cache. Data plane (model) samples thought / tool JSON / final text. Data plane (executor) runs tools and maps errors (03). Persistence is the **checkpoint** (super-step snapshot) plus plan object / Reflexion store — **not** the prompt-cache KV. Tool proxies never take IAM from model JSON. Telemetry is the only place hop histograms, `total_cost_usd`, and repeating-tool fingerprints are authoritative.

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│ CLIENTS                                                                         │
│  SSE support  │  batch research jobs  │  HITL interrupt  │  webhooks / Signals │
└────────────┬────────────────────────────────────────────────────────────────────┘
             │ TLS + session JWT + thread_id=tenant:user:session + correlation-id
             ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│ CONTROL PLANE  (your process — loop, not the GPU)                               │
│                                                                                 │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌───────────┐  │
│  │ Edge       │─▶│ Policy     │─▶│ LOOP       │─▶│ HOP        │─▶│ HASH      │  │
│  │ auth, RPM  │  │ PII redact │  │ CONTROLLER │  │ COUNTER    │  │ CIRCUIT   │  │
│  │ breaker per│  │ tool RBAC  │  │ ReAct |    │  │ max_turns  │  │ (tool,    │  │
│  │ (vendor,   │  │ FROZEN     │  │ P&E |      │  │ recursion_ │  │  args) N=3│  │
│  │  model)    │  │ user goal  │  │ Reflection │  │ limit      │  │ empty N=6 │  │
│  │            │  │ ≠ prompt   │  │ dispatcher │  │ remaining_ │  │ block tool│  │
│  │            │  │            │  │            │  │ steps      │  │            │  │
│  │            │  │            │  │            │  │ max_budget │  │            │  │
│  │            │  │            │  │            │  │ _usd       │  │            │  │
│  └────────────┘  └─────┬──────┘  └─────┬──────┘  └─────┬──────┘  └─────┬─────┘  │
│                        │               │               │               │        │
│                        ▼               ▼               ▼               ▼        │
│                 ┌──────────────────────────────────────────────────────────┐    │
│                 │ ORCHESTRATOR  Temporal Workflow = tenant:thread          │    │
│                 │  ├─ Activity: LLM (max_retries=0 in SDK)                 │    │
│                 │  ├─ Activity: ToolDispatcher per call_id (03)            │    │
│                 │  ├─ CHECKPOINTER snapshot at super-step (sync/async/exit)│    │
│                 │  ├─ CRITIC optional; skip unless hard oracle             │    │
│                 │  └─ HITL interrupt_on irreversible tools                 │    │
│                 └──────────────────────────┬───────────────────────────────┤    │
│  ┌────────────┐  ┌────────────┐            │            ┌──────────────────┐    │
│  │ Checkpt    │  │ Circuit    │◀───────────┘───────────▶│ Fallback         │    │
│  │ Postgres / │  │ breaker    │                         │ primary model →  │    │
│  │ Redis      │  │ per class  │                         │ secondary →      │    │
│  │ thread_id  │  │ LLM ≠ tool │                         │ deterministic    │    │
│  │ pending    │  │            │                         │ degraded JSON    │    │
│  │ writes     │  └────────────┘                         └────────┬─────────┘    │
│  └────────────┘                                                  │              │
└──────────────────────────────────────────────────────────────────┼──────────────┘
                                                                   │
          ┌────────────────────────────────┬───────────────────────┘
          │ chat / agent SSE, REST         │
          ▼                                ▼
┌─────────────────────────────────┐  ┌────────────────────────────────────────────┐
│ DATA PLANE  GENERATION          │  │ DATA PLANE  EXECUTOR (your workers)        │
│ (provider-owned on hosted APIs) │  │ model NEVER holds IAM or Stripe sk         │
│                                 │  │                                            │
│  Tokenizer → Prefill → Decode   │  │  complete JSON only (03); remaining_steps  │
│  Thought tokens + tool JSON     │  │  <2 → wrap-up, do NOT dispatch tools       │
│  stop: end_turn / tool_use /    │  │  is_error mapped honestly (03)             │
│    pause_turn (server inner) /  │  │  Hosted/server tools invert this box:      │
│    max_tokens / refusal         │  │  inner agentic loop; YOUR outer fuse still │
│                                 │  │  owns the client hop/dollar cap            │
└────────────┬────────────────────┘  └─────────────────────┬──────────────────────┘
             │                                             │
             │  untrusted planner (thought / plan JSON)    │ side effects
             ▼                                             ▼
┌─────────────────────────────────┐  ┌────────────────────────────────────────────┐
│ TOOL PROXIES  (MCP / adapters)  │  │ PERSISTENCE LAYER                          │
│ Zero-Trust: RFC 8707 audience;  │  │                                            │
│ NO token passthrough            │  │  ┌──────────────────┐  ┌─────────────────┐ │
│ identity = session JWT /        │  │  │ Checkpointer     │  │ Reflexion store │ │
│ Temporal info — never model JSON│  │  │ thread_id, plan, │  │ / critic buffer │ │
│  ┌──────────┐  ┌─────────────┐  │  │  │ past_steps, HITL │  │ TTL blob; NOT   │ │
│  │ Stripe / │  │ CRM / MCP   │  │  │  │ pending writes   │  │ in every ckpt   │ │
│  │ refund   │  │ tools/call  │──┼──│  │ durability mode  │  │ messages[]      │ │
│  │ HITL     │  │ SSRF filter │  │  │  └──────────────────┘  └─────────────────┘ │
│  └──────────┘  └─────────────┘  │  │  ┌──────────────────┐  ┌─────────────────┐ │
│  observations = DATA not policy │  │  │ Idempotency (03) │  │ Soft caches     │ │
└─────────────────────────────────┘  │  │ saga / outbox    │  │ prompt-cache KV │ │
                                     │  │ Temporal history │  │ (02); not RPO=0 │ │
                                     │  └──────────────────┘  └─────────────────┘ │
                                     └────────────────────────────────────────────┘
                                                            │
┌───────────────────────────────────────────────────────────┴─────────────────────┐
│ TELEMETRY / OBSERVABILITY SINKS                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐ │
│  │ Audit (WORM) │  │ Metrics      │  │ Traces       │  │ Usage (authoritative │ │
│  │ cid, tenant  │  │ hop p50/p95  │  │ gateway→LLM  │  │ on terminal event)   │ │
│  │ hop, pattern,│  │ tool_repeat, │  │ →executor→   │  │ input, cache_read,   │ │
│  │ SHA-256 args │  │ remaining_   │  │ HITL wait    │  │ cache_write, output, │ │
│  │ frozen_goal  │  │ steps,       │  │ PII stripped │  │ thinking_tokens,     │ │
│  │ hash, stop_  │  │ breaker,     │  │ in prod      │  │ total_cost_usd,      │ │
│  │ reason, HITL │  │ replan count │  │              │  │ num_turns            │ │
│  └──────────────┘  └──────────────┘  └──────────────┘  └──────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────────┘
```

**Planes (do not couple):**

| Plane | Owns | Failure if coupled |
| --- | --- | --- |
| **Control** | Dispatcher, fuses, hash circuit, checkpointer key, critic gate, frozen goal | Model “finish[]” trusted; Stripe in the same pass as REFLECT |
| **Generation data** | Sample thought / tool JSON / text | Executor runs on `pause_turn` as if `end_turn` |
| **Executor data** | Tools, `is_error`, timeouts | Silent empty success → ReAct loops |
| **Tool proxies** | MCP/Stripe with audience-bound tokens | Token passthrough; observation-as-policy |
| **Persistence** | Checkpoint + plan object + Reflexion TTL | Pod kill mid-HITL refund; Reflexion PII in every row |
| **Telemetry** | Hop audit, cost, repeats | Finance dashboards that ignore 25-hop runaways |

Hosted **server tools** invert the executor: the provider runs an **inner** agentic loop and may return `pause_turn` when *that* loop hits its cap. Your outer loop still owns the **client** fuse. Anthropic 2024: **workflows** = predefined paths; **agents** = LLM directs process — production mixes both.

### 1.2 End-to-end request flow

1. **Ingress.** SSE (support ReAct) or REST/batch (research P&E). Gateway stamps `correlation_id`, binds `thread_id = tenant:user:session` (a constant string **shares history across tenants**), consults the **per-(vendor, model)** breaker **and** hop-budget remaining.
2. **Policy + frozen goal.** Detect → redact PII **before** the transcript is checkpointed or traced. Persist the **user objective** as an immutable field. Tool RBAC maps `(principal, tenant, tool, args_shape)` → allow / deny / HITL. Per-hop **allowlists** are capability reduction, not authz (03).
3. **Pattern dispatch.** If next action must depend on the last observation in an unpredictable way → **ReAct** with a tight hop cap. If many **independent** calls → **P&E / DAG** (ReWOO / LLMCompiler) so the planner prefix is not re-sent every search. Do not put LATS/MCTS on a chat SLO.
4. **Hop / dollar / super-step fuses (before sample).** `assert hops < max_turns`; `remaining_steps >= 2` before a tool round (need tool super-step **then** model); `spend + next_call <= max_budget_usd` (pad Claude’s after-the-fact check). Hash circuit: if `(tool, canonical_args)` failed **N=3**, inject a hard observation and **remove** that tool for the rest of the run.
5. **Checkpointer.** Snapshot **at super-step boundaries** (`sync` if HITL money). No `thread_id` ⇒ no save, no interrupt resume. Durability `"exit"` **loses** mid-run state on pod kill.
6. **Model Activity.** One process-wide async client; `max_retries=0` if Temporal owns HTTP (03). Stream until complete tool JSON — never execute `input: {}` at `content_block_start`.
7. **Parse stop_reason.** `end_turn` / no `tool_calls` → maybe critic (only with oracle) → END. `tool_use` → executor. `pause_turn` → **resend** assistant content. `max_tokens` mid-tool → fail closed (03). `refusal` → fail closed. OpenAI handoff → consumes a turn, swap agent, re-enter.
8. **Execute-gate.** Unknown name → model-visible allowlist (03). RBAC re-check. Irreversible (`issue_refund`, `send_email`, prod write, export) → **HITL** with uneditable sanitized args; do not auto-exec. Parallel **reads** may gather; mutating tools sequential (03).
9. **Observation as data.** Wrap tool I/O in delimiters (02). If observation tries to rewrite the **goal** (new email dest, new exfil tool) → HITL, do **not** replan into the attack. Empty/useless search → P&E replan (cap 2–3), not blind ReAct `Lookup` loops.
10. **Critic (optional).** Hard oracle (unit tests, interpreter, exact match) > PRM > LLM-as-judge. Same-model FEEDBACK without a checker is Huang’s trap — **log a warning and skip**. Reflexion hints go in a **TTL store**, not the cached prefix (02).
11. **Replan / join.** P&E: after workers, Joiner emits remaining steps **or** a `Response`. Cap `max_replans`. DAG node invalidating `$k` → Joiner, not a full user-visible restart. Do not replan every hop.
12. **Emit + audit.** Terminal usage is the invoice (all hops). WORM: hop, pattern, `stop_reason`, hashed args, frozen-goal hash, HITL decision, `thread_id`, `correlation_id`. `MaxTurnsExceeded` / `error_max_turns` / “Sorry, need more steps…” are **failed tasks** in eval even if HTTP 200.

**Interview talking point:** “The model is an untrusted planner. I ship three clocks — turns, super-steps, dollars — plus a hash circuit on `(tool, args)`. Remaining-steps wrap-up beats a raw recursion error in UX; it is still a failed run.”

---

## 2. Core Mechanics & Algorithms

### 2.1 ReAct: thought–action–observation

Yao et al. (ICLR 2023) augment the action space: language **thoughts** do not touch the environment; domain **actions** do. Trajectory: interleaved `Thought → Action → Observation`. HotpotQA/FEVER used **dense** TAO; ALFWorld/WebShop used **sparse** `think:` (observation `"OK."`). HotpotQA tools: `Search[entity]`, `Lookup[keyword]`, `Finish[answer]` against a weak Wikipedia API — paper loop `range(1, 8)` (**7** steps then forced `finish[]`); FEVER **5**; ALFWorld notebook **49**. Of already-correct trajectories, full 7/5 steps were only **0.84% / 1.33%**. Grounding kills hallucination (ReAct failure-hallucination **0%** vs CoT **56%**); the same interleaving **reduces reasoning flexibility** — **47%** of ReAct failures include **repetitive TAO**. Production implication: **external** loop breaker.

PaLM-540B: ReAct **loses** HotpotQA EM to CoT (27.4 vs 29.4) and **wins** FEVER (60.9 vs 56.3). Paper SoTA is the **hybrid** (ReAct ↔ CoT-SC). ALFWorld: ReAct **71** vs BUTLER **37** (+34 pp); sparse thoughts beat dense inner-monologue (**71** vs **53**).

### 2.2 Interleaved vs batched (different meters)

| Mode | One model hop contains | Observation timing | Meter |
| --- | --- | --- | --- |
| **Interleaved TAO** | One thought + one action | After every action | Yao notebooks; LangGraph model ⇄ tools |
| **Batched actions, interleaved hops** | N parallel `tool_calls` | All results in **one** following user message | OpenAI parallel tools = **one turn** |
| **Batched plan, then tools** | Planner DAG/list **without** tool output | Workers run; Solver sees evidence once | ReWOO; LLMCompiler |
| **Hidden interleaved** | Reasoning + tools inside a provider turn | Server tools may `pause_turn` | Anthropic server-tool loop |

ReWOO quadratic: hosted APIs are **stateless**; interleaved hops re-send \(C+S\) every step (**linear in \(k\)** on prefix, **quadratic** in trajectory fragments). Prompt caching (02) flattens \(C\) **iff** the prefix is byte-stable; it does **not** flatten the growing observation suffix. LangGraph Pregel: `recursion_limit` counts **super-steps**. Classic ReAct ≈ **2** super-steps per tool round → historical default **25** ≈ **12** tool rounds then `GraphRecursionError`; docs **1.0.6+** default **1000** ≈ **500** rounds — still need a hash circuit.

### 2.3 Production ReAct runtimes

**LangChain `create_agent`** (replaces deprecated `create_react_agent`): call model → if `tool_calls` then tools node → repeat. Middleware wraps the loop (`before_agent` / `wrap_tool_call` / …). Deprecated executor: if `remaining_steps < 2` and the model still wants tools → **“Sorry, need more steps to process this request.”** and **no** `GraphRecursionError`. `remaining_steps ≈ recursion_limit − total_steps_taken`. Route on `RemainingSteps` **before** the fuse. Step counter: `config["metadata"]["langgraph_step"]`.

**OpenAI Agents SDK `Runner`:** invoke LLM → final output (text of `output_type` and **no** tool calls) **or** handoff **or** tools and re-enter. `DEFAULT_MAX_TURNS = 10`; exceed → `MaxTurnsExceeded`; `None` disables; `error_handlers={"max_turns": ...}` can return controlled text. Guardrails abort **outside** the turn counter.

**Anthropic Messages:** `while stop_reason == "tool_use"`: execute every client tool, one user message with **all** `tool_result`s first (03). Exit `end_turn` / `max_tokens` / `stop_sequence` / `refusal` / `model_context_window_exceeded`. `pause_turn` = server-tool inner cap.

**Claude Agent SDK:** `max_turns` / `max_budget_usd`; hit → `error_max_turns` / `error_max_budget_usd` with **no** `result` text. Stall watchdogs: async subagent **600 s**; stream idle **300 s** — hung **sockets**, not repeating tools.

### 2.4 Plan-and-execute / ReWOO / LLMCompiler

**Plan-and-Solve / PS+** is **one generation** (“devise a plan, then carry it out”) — not a tool loop unless you split nodes. LangGraph P&E: `planner` → `agent` (`plan[0]`) → `replan` → END or agent. State: `input`, `plan`, `past_steps`, `response`. Template replans **after every executed step** — cap `max_replans` yourself. Serial; embarrassingly parallel work wants a DAG.

**ReWOO:** Planner → Worker(s) → Solver. Planner emits `(Plan, #E_s)` placeholders; workers fill evidence **without** the planner seeing observations. HotpotQA gpt-3.5: **42.4** acc / **1,986** tokens / **$3.97/1k queries** vs ReAct **40.8** / **9,795** / **$19.59**. Tool-failure ablation: ReAct **−40.8** acc, ReWOO **−29.2**. Extraneous tools **hurt**.

**LLMCompiler:** Planner DAG with `$k` → Task Fetching Unit → concurrent Executor → optional Joiner. vs ReAct†: up to **3.7×** latency, **6.7×** cost, **~9 pp** accuracy. HotpotQA: **3.37×** cheaper, **1.80×** faster. Movie Rec: **6.73×** / **3.74×**. ~**10%** of HotpotQA ReAct needed **>4** calls (looped/divergent). WebShop vs LATS: **10.72 s** vs **1,066 s** (**101.7×**). Streaming the DAG hides planner latency (up to **1.3×** extra on ParallelQA). Residual: Movie Rec planner **1.88 s** + answer **1.62 s** — more than half of e2e when tools are fast.

**When to replan (control plane, not a vibe):** empty/useless search → yes (reformulate in the **plan**); leaf `is_error` with an alternate tool → maybe; verifier fail → yes or new Reflexion trial; DAG `$k` invalidated → Joiner; observation injects a **new goal** → **no**, HITL. HuggingGPT: global plan in **one** planner query vs BabyAGI iterative next-task (can loop forever).

### 2.5 Reflection and the Huang trap

**Reflexion** (across **trials**): Actor (often ReAct) → evaluator → self-reflection LLM → **episodic memory** → next trial. AlfWorld **+22 pp** over ReAct across **12** trials (ReAct plateaus trials 6–7); HotpotQA **+20 pp**; HumanEval Python pass@1 **91.0** vs GPT-4 **80.1**. Ablation: **without tests, reflection hurts (52% vs 60%)**. WebShop terminated after **4** trials with no useful reflections.

**Self-Refine:** same LLM as generator, feedback, refiner; **M ≤ 4**; ~**20%** abs average vs one-shot — **math barely moves** (GPT-4 92.9→93.1); dialogue/constrained generation move tens of points. Up to **1 + 4×(fb+refine) = 9** LLM calls vs 1.

**Huang et al. ICLR 2024 — intrinsic self-correction:** without oracle labels, accuracy **drops**. GPT-3.5 GSM8K **75.9 → 74.7** (round 2, **5** calls); CommonSenseQA **75.8 → 41.8**; GPT-4 GSM8K **95.5 → 89.0**, HotpotQA **49.0 → 43.0**. Oracle-label loops look great **because the ground truth prevents correct→incorrect edits**. GPT-3.5 keeps the initial GSM8K answer **74.7%** of the time; when it edits, it more often **breaks** a correct answer. Multi-agent debate **no better than self-consistency** at equal \(N\). Kamoi survey: bottleneck is **feedback generation**; intrinsic self-correction on general tasks is unproven.

Four mechanisms that are **not** interchangeable: **tool-error** (`is_error` / stderr); **constitutional** self-critique (style/harmlessness); **verifier** (tests, interpreter, PRM, judge); **debate** (cost × rounds). CRITIC **with tools** helps; CRITIC **w/o Tool** can **degrade** (SVAMP **−1.8**; toxicity **worsens**). Production rule: **no critic without a checker** on math/code/facts; cap M=1 on open-ended chat.

### 2.6 Fuses (not portable)

| Fuse | Unit | Default (verify the version you ship) | On trip |
| --- | --- | --- | --- |
| OpenAI `max_turns` | Model invocation **including** tools in that invocation | **10**; `None` disables | `MaxTurnsExceeded` or handler |
| Claude Agent SDK `max_turns` | **Tool-use** round trips only | **None** (unlimited) | `error_max_turns` (no `result`) |
| Claude `max_budget_usd` | Client-side USD estimate | **None** | `error_max_budget_usd`; overshoot **one** call |
| LangGraph `recursion_limit` | Pregel **super-steps** | historically **25**; docs **1.0.6+** **1000**; Deep Agents subagents still **25** | `GraphRecursionError` |
| `remaining_steps` | Super-steps until limit | derived | Soft “Sorry, need more steps…” |
| Google ADK `LoopAgent.max_iterations` | Full cycles over `sub_agents` | unset = until escalate | Stop or `escalate=True` |
| ReAct paper | Env steps | HotpotQA **7**, FEVER **5**, ALFWorld **49** | Forced `finish[]` |
| AutoGPT hash circuit | Identical `(tool, args)` | **3** failures; **6** empty `{}` | Hard-stop / abort stream |

```python
graph.invoke(inputs, {"recursion_limit": 50})  # correct — not under configurable
```

Infinite-loop **detection** is not `recursion_limit` (that fires on a *healthy* long research trace): (a) identical `(tool, args)` N times; (b) oscillating name-window A-B-A-B; (c) no new information in last-k `tool_result`s; (d) wall-clock stall (Claude 300 s / 600 s). (a)–(c) **before** the next decode; (d) in HTTP so a hung provider does not hold a worker. LangGraph has **no** built-in dollar cap — add middleware on `usage` (01) and jump to END. Ship **all three** clocks in production.

### 2.7 State machines

```
ReAct:     START → MODEL ⇄ TOOLS → END
           exit MODEL when no tool_calls / stop_reason ≠ tool_use
           fuse: max_turns | recursion_limit | remaining_steps | hash circuit

P&E:       START → PLAN → EXEC(plan[0] or DAG-ready) → REPLAN ⇄ EXEC → END
           REPLAN emits remaining steps | Response
           fuse: max_replans + per-exec inner max_turns
           DAG: PLAN → FETCH-READY ⇉ EXEC → JOIN → (REPLAN | END)

Reflection: TRIAL(ReAct) → EVAL → (END if pass) → REFLECT → MEMORY → TRIAL
            fuse: max_trials (AlfWorld 12; WebShop died at 4)
            Self-Refine: GEN ⇄ (FEEDBACK → REFINE) with M≤4  (no env trial)
```

**Control-plane state** = node + hop counters + plan object + memory buffer + frozen goal. **Data-plane state** = transcript and tool I/O. Mixing them is the dominant correctness failure.

**HTTP retry (wires, not hops):**

```
                    Retry-After ∈ (0, 60s]              attempts exhausted
  ┌──────────┐  HTTP 408/429/5xx/529      ┌─────────┐  ─────────────────▶ FAIL
  │  SEND    │ ─────────────────────────▶ │  WAIT   │
  └────┬─────┘  400 / 401 / 403           │ jitter  │
       │        spend-cap 429 (no RA)     │ cap 8s  │
       │        MaxTurns / Budget         │         │
       │        ────────────────────────▶ FAIL      │
       │ success                          └────┬────┘
       ▼                                       │
     DONE ◀────────────────────────────────────┘  retry SEND
```

**Hop loop:**

```
  ┌──────────┐  hops>=max_turns / budget  ┌────────────┐
  │  MODEL   │ ─────────────────────────▶ │ FUSE END   │  (partial; not success)
  └────┬─────┘  remaining_steps<2 + tools │ wrap-up    │
       │        ────────────────────────▶ │ "need more │
       │ end_turn / no tool_calls         │  steps"    │
       ├────────────────────────────────▶ END          │
       │ pause_turn ── resend assistant   └────────────┘
       │ tool_calls
       ▼
  ┌──────────┐  irreversible          ┌─────────┐
  │ EXECUTOR │ ─────────────────────▶ │ HITL    │── resume + re-RBAC
  │ hash N=3 │  identical fail        └─────────┘
  │ empty N=6│ ─────────────────────▶ HARD OBS / abort stream
  └────┬─────┘
       │ observations as DATA
       ▼
  NEXT MODEL HOP   (do not let obs rewrite frozen_goal)
```

**Complexity.** Each interleaved hop is a full `messages.create`: billed uncached suffix + cache read of frozen prefix (02) + output. Hop count \(k\): prefix term **Θ(\(k\))** without cache, **Θ(1)** cache-read after warm + **Θ(\(k\))** growing observations. ReWOO/LLMCompiler planner+solver: **Θ(1)** planner prefix + **Θ(workers)** tool I/O. Hash circuit: **O(1)** per call (canonical JSON + SHA-256). Super-step arithmetic is **O(nodes sequentially)**, not O(tool calls).

### 2.8 Invariants

1. The model never executes tools and never owns process termination.
2. `max_turns`, `recursion_limit`, and `max_budget_usd` are **different clocks** — ship all three.
3. `remaining_steps < 2` ⇒ wrap-up; do not start a tool round you cannot finish.
4. Identical `(tool, canonical_args)` **N=3** failures ⇒ hard observation + block; **N=6** empty calls ⇒ abort stream.
5. Critic/Reflexion without an oracle is **off** (Huang). Store hints **out of** the cached prefix.
6. Freeze the user goal; observations cannot add `send_email` / new destinations without HITL.
7. `pause_turn` ≠ `end_turn`. Soft “need more steps” ≠ eval success.
8. Parent `recursion_limit` does not apply to Deep Agents subagents unless propagated.
9. Checkpointer key = `tenant:user:session`; durability `sync` around irreversible HITL.
10. Idempotency of downstream POST is **independent** of the hop hash circuit (03).

---

## 3. Token Economics & NFR Analysis

List prices and cache multipliers: **see 01**. Tool-schema tax every hop: **see 02**. This section prices **hops**. `$ per 1k completed tasks` is **[inferred]** from published rates × stated hop counts, not a vendor SKU. Formula (same as 01; \(T_{\mathrm{out}}\) includes thinking):

\[
C = n \cdot \frac{T_{\mathrm{miss}} P_{\mathrm{miss}} + T_{\mathrm{hit}} P_{\mathrm{hit}} + T_{\mathrm{write}} P_{\mathrm{write}} + T_{\mathrm{out}} P_{\mathrm{out}}}{10^{6}}
\]

### 3.1 Cost per 1k **completed loop runs**

**[inferred] support-agent hop, GPT-6-sol short-context (01: $2 in / $0.20 cached / $2.50 cache write / $10 out).** Assumptions: 8k frozen prefix (system+tools), 400 output tokens/turn, 600 new input tokens/turn, cache hit on prefix from turn 2. Turn 1 writes 8k at $2.50/M + 0.4k out; later turns read 8k at $0.20/M + 0.6k uncached in + 0.4k out.

| Model hops | Uncached in | Cached in | Out | **[inferred] $ / run** | **$ / 1k runs** |
| --- | --- | --- | --- | --- | --- |
| 1 (no tools) | 8.0k write | 0 | 0.4k | 0.024 | **24** |
| 3 (2 tool rounds) | 8.0k write + 1.2k | 16k | 1.2k | 0.0376 | **38** |
| 10 (Agents SDK default) | 8.0k write + 5.4k | 72k | 4.0k | 0.0852 | **85** |
| 25 model calls | 8.0k write + 14.4k | 192k | 10.0k | 0.187 | **187** |

If the prefix **mutates** every hop (tool shuffle, timestamp — 02), ten hops ≈ **[inferred] $0.20/run ($200/1k)** — ~**2.3×** the cached 10-hop row, **before** growing observations. Sonnet 5 same shape is numerically close on this toy; the real delta is **hidden tool-system tokens** (354/474 — 02) **every** hop.

Self-Refine: up to **9** calls vs 1. If each is ~2k in / 800 out on Sonnet 5 uncached: **[inferred] $0.108/run ($108/1k)** vs one-shot **$12/1k** — **9×** for **~0 pp on math**. Reflexion **12 × 10-hop** cached sol ≈ **[inferred] $1.02/task** model-only — a **batch eval** pattern, not a chat SKU, unless trials cap at 2–3 with a hard evaluator. Huang GPT-4 GSM8K: **5×** spend to **lose 6.5 pp**. LATS WebShop **1,066 s** — not a hot-path SKU.

**[inferred] mix** (1k conversations/day, 70% 1-hop, 25% 3-hop, 5% 10-hop, 80% prefix cache, ignore tool SaaS): \(0.7×1000×\$0.024 + 0.25×1000×\$0.038 + 0.05×1000×\$0.085 ≈ **\$31/day model**\). Add 200 web searches × $10/1k = **$2**. Runaway 25-hop fleet at 1k/day = **~$187/day (~6×)**. Reflection-on-every-ticket (9 calls) on the 5% tail: **+~$5/day**; on everyone: **+$108/day**.

Research agent (plan once + 8 parallel workers + 1 solver): structural win is **do not re-send the planner prefix every search** (LLMCompiler Movie Rec **$3.04/1k** at 2023 tokens; **20k** ReAct input vs **2.8k**). Workers on Haiku/luna (01).

### 3.2 Latency SLA targets

> ⚠️ Limited public data available for **vendor p50/p95/p99 of agent loops** — OpenAI/Anthropic/Google do **not** publish those percentiles. Numbers below are paper **means**, documented **caps**, and **[inferred] policy** for *your* SLO doc. Do not invent a p95 in an architecture review.

| Source | Number | Kind |
| --- | --- | --- |
| ReAct HotpotQA / ALFWorld | cap **7** / **49** | Paper fuse, not p95 |
| LLMCompiler HotpotQA | ReAct† **7.12 s**; Compiler **3.95 s**; OAI parallel **4.42 s** | **Mean**, 2023 GPT-3.5-turbo-1106 |
| LLMCompiler Movie Rec | ReAct† **20.47 s**; Compiler **5.47 s** | Mean |
| LLMCompiler WebShop | ReAct **5.98 s**; LATS **1,066 s**; Compiler **~11 s** | Mean; LATS N=50 |
| OpenAI default / LangGraph 1.0.6+ | **10** turns / **1000** super-steps | Caps |
| Claude stall watchdog | **600 s** async subagent; **300 s** stream idle | Stall, not hop p95 |

**[inferred] sequential ReAct:** \(T \approx \sum_i (\mathrm{TTFT}_i + T_{\mathrm{decode},i} + T_{\mathrm{tool},i})\). p99 is the **slowest tool** + **longest decode**, not average TTFT (01/03 nested timeouts). Fan-out DAG: p99 ≈ max(worker p99) + join LLM. HITL: p99 is the **human SLA**.

**[inferred] policy targets** (not vendor guarantees):

| Metric | Target | Mitigation |
| --- | --- | --- |
| **p50** hops / wall (support ReAct) | **1–3 hops**; wall **< 8 s** | Parallel independent **reads** in **one** turn (03); cache-stable tools prefix (02); `max_turns=8–10`; REST tool p95 **<800 ms** |
| **p95** hops / wall | **≤ 8 hops**; wall **< 20 s** unless HITL | Hash circuit N=3; `RemainingSteps` wrap-up; do not Self-Refine the tail; P&E when ≥3 independent searches |
| **p99** hang | Fail closed on stall watchdog / Activity timeout; no 10-min empty-call stream | Nested timeouts ordered; **one** retry owner; hash circuit; `max_budget_usd`; HITL p99 = human SLA not model |

Measure yourself: `n_turns`, cached vs uncached tokens, tool RTT histogram, `langgraph_step`, `total_cost_usd`, `tool_repeat_count`.

### 3.3 Throughput and back-pressure

LLM RPM/ITPM: **see 01**. Loop fleets multiply by **hops**: 100 concurrent tickets × 10 turns = **1,000** model calls in flight if uncapped. Web search SaaS is **$10/1k calls** (01) — cap search count per job.

**Back-pressure design:**

1. Admit iff LLM breaker ∈ {closed, half-open} **and** hop budget remaining **and** tool-class semaphore has room.
2. Shed in order: disable parallel **writes** → skip critic → force P&E join → deterministic degraded JSON. Do not raise `recursion_limit` to 9999 as a “fix.”
3. Partition `prompt_cache_key` by tenant (02 overflow **>~15 req/min** per key).
4. Batch research: worker pool sized to **tool** RPM (search index, not GPU). Inner ReAct `max_turns=3` per plan node so a poisoned observation wastes **3** hops, not **1000** super-steps.

### 3.4 Non-functional requirements

| NFR | Working target | Tension |
| --- | --- | --- |
| **Availability** | 99.9% **gateway**. Loop still ends on fuse (partial answer + `stop_reason`). Multi-vendor LLM fallback for 503/529 | Failover **busts** prefix cache (02); never failover a 400; `error_max_turns` is not a 200-success |
| **RPO** | Checkpointer + frozen goal + pending writes: **0** for HITL-irreversible (`sync`). Reflexion store TTL hours. KV/prompt-cache: **minutes**, best-effort | `"async"` durability races the next super-step; `"exit"` RPO = whole run |
| **RTO** | Interactive: LLM failover **< 1 s** (breaker already open). Resume HITL with **same** `thread_id` + `Command(resume=...)`. In-flight payment: **same** idempotency key (03) | Fast failover vs bit-identical tokens (T>0); node restart **replays** side effects unless idempotent |
| **Consistency** | Plan object + `past_steps` are the DAG truth. Model text: at-least-once retry **changes tokens**. Exactly-once is a **lie** without the downstream store (03) | Replan must not flip frozen goal; pending writes skip completed siblings |
| **Compliance** | Regional +10% / Anthropic 1.1× US geo (01); ZDR **excludes** batches/files; traces default **include** tool I/O — set `trace_include_sensitive_data=false`; checkpoints are PII stores | Residency vs latency vs DLP on 25-hop transcripts |
| **Cost vs latency** | Cached 10-hop **[inferred] $85/1k** vs 25-hop **$187/1k** vs uncached 10-hop **~$200/1k** vs Self-Refine **$108/1k** | Paying 12 Reflexion trials on a chat SLO; LATS **1,066 s** |
| **Cache vs tenancy** | Frozen tools/system prefix; summaries in the **mutating suffix** (02). `thread_id` includes tenant | Hit rate vs isolation; compaction that rewrites prefix = miss storm |

---

## 4. Distributed Resilience & Security

### 4.1 Durable loop (Temporal / Kafka / checkpointer)

Application state ≠ KV cache. Compose **LangGraph cognition inside Temporal/Inngest**: every **LLM call** and every **tool I/O** is an **Activity**; the Workflow is the deterministic loop. Disable nested SDK retries (`max_retries=0`) so **one** owner retries (03). `workflow-id = tenant:thread_id` so two gateways cannot run the same loop.

**Checkpointer:** snapshot at super-step boundaries. `configurable.thread_id` is the primary key. Durability: `"sync"` before the next super-step (safest; use around refund HITL); `"async"` while the next step runs (small risk of losing the latest checkpoint); `"exit"` only on graph exit — **lose** all mid-run state on pod kill. **Pending writes:** if one node fails mid-super-step, successful siblings’ writes are kept so resume does not re-run them. Crash mid-hop: the interrupted **node restarts from the top** — side effects before `interrupt()` run **twice** unless idempotent (03 Stripe keys; Temporal Activity id). `interrupt(value)` requires a checkpointer; multiple interrupts match resume values **by order**.

OpenAI `Runner.run` is **stateless** across calls unless Session / `RunState` / `previous_response_id`. `cancel(mode="after_turn")` is cooperative — cancelling mid-stream without persisting tool results retries the tool on resume. Claude: `continue_conversation` / `session_id`. ADK `LoopAgentState`: `times_looped`, `current_sub_agent`.

Compaction (02) is a **context** fuse, orthogonal to `recursion_limit`. A 25-hop transcript blows the window even at recursion 1000. Summaries must land in the **mutating suffix**. Reflexion buffers belong in a Store/blob with TTL, not in every checkpoint `messages` channel (Postgres bloats linearly with hops).

**Kafka / outbox:** `loop.intents` (goal + thread_id **before** first model call), `loop.hops`, `tool.results`, `hitl.decisions` → **Signal** the Workflow, `loop.dlq`. Compaction on `thread_id` keeps a snapshot; the full log is chain-of-custody. Poison: skip + alert after N handler crashes.

> ⚠️ Gap: research has no measured Temporal replay cost for multi-MB 25-hop transcripts and no LangGraph built-in dollar cap. Map dollars onto middleware that reads `usage` and jumps to END; keep tool snippets in object storage (Continue-as-needed), not Workflow history.

### 4.2 Max-iter is a circuit, not a success signal

`GraphRecursionError` means **you did not design a stop**. Catching it for a partial answer is acceptable **only** if pending writes persisted and no payment Activity is in doubt. OpenAI `error_handlers["max_turns"]` is the explicit policy; Claude `error_max_turns` has **no** `result` — do not treat it as an assistant message. `create_react_agent`’s “Sorry, need more steps…” is a **silent success-shaped** failure (SQL agents historically hit this under default 25). Raise `recursion_limit` **and** add a completion node; metric that exact string. Raising Deep Agents dcode to **2000** only **delays** an infinite loop — pair with `--max-turns` / hash circuit. Propagate limits through `SubAgentMiddleware`.

### 4.3 Failure taxonomy, poison repeating, circuit breaker, fallbacks

| Class | Examples | Handler |
| --- | --- | --- |
| **Transient** | 408/429/5xx/529, TLS reset, MCP disconnect | Full jitter; same idempotency key; last-good catalog |
| **Permanent** | 400 schema, 401/403, `refusal`, spend-cap 429 | Fail the hop; **do not** failover schema 400s |
| **Poison pill (loop)** | Identical `(tool, canonical_args)` failures; empty `input: {}`; A↔B oscillation; greedy TAO repeat (**47%** ReAct fail bucket) | N=3 hard-stop that tool; N=6 abort stream; name-window for ping-pong |
| **Semantic** | PlanFlip / goal hijack via tool output; schema-valid unauthorized refund | Frozen goal + RBAC + HITL; not a retry |
| **Soft fuse as success** | “Sorry, need more steps…”; Claude `error_max_turns` parsed as text | Metric + eval fail |
| **Fuse mismatch** | Subagent 25 vs parent 1000; `pause_turn` as `end_turn`; `max_tokens` mid-`tool_use` | Propagate config; resend; raise `max_tokens` |
| **Critic regression** | Huang −6.5 pp; Reflexion without tests −8 pp | Disable critic unless oracle |

**Poison repeating circuit (control plane):**

1. Canonicalize args (JSON key sort; strip ephemeral timestamps).
2. Key = `(tool_name, args_hash)` or include `output_hash` for “same in and out.”
3. After **N=3** identical **failures**: hard observation (“do not retry; answer or replan”) and optionally remove the tool from `allowed_tools` (cache-preserving allowlist — 03).
4. After **N=6** empty calls: abort the **stream**.
5. Pagination: cap `page` / `offset`. Oscillation: last-\(k\) tool-name window.

A looping `create_charge` with a **new** model-invented UUID is a **duplicate-charge** bug (03) — hop hash ≠ Stripe idempotency key. Derive money keys from `call_id` / workflow ids.

**Circuit breaker** (Resilience4j; one per **(provider, model)** for LLM **and** one per **tool-class**). Open on high **5xx/529/timeout** rate. **Do not** open solely on 429-with-Retry-After. Half-open: probe with a **cheap read**, not `issue_refund`.

```
           5xx/529/timeout rate ≥ threshold           probe success
  ┌────────┐  ──────────────────────────────────▶  ┌──────┐  ──────▶ CLOSED
  │ CLOSED │                                       │ OPEN │
  └───┬────┘  429 with Retry-After = throttle      └──┬───┘
      │       (stay CLOSED; sleep)                    │ timer (e.g. 30 s)
      │ success resets window                         ▼
      │                                          ┌──────────┐
      └──────────────────────────────────────────│ HALF_OPEN│── probe fail ──▶ OPEN
                                                 │ 1 cheap  │
                                                 │ read     │
                                                 └──────────┘
```

**Fallback chain:** primary (Sonnet 5 / GPT-6-sol) → secondary vendor (same IR) → **deterministic** `{"status":"degraded"}` (no charge, no email). Tool-class open → queue / HITL, not a second processor. PermanentError on schema / RBAC **does not** failover. Retry amplification: LLM timeout 60 s → tool 55 s → both retry. Fix: one retry owner; hop cap; hash circuit; nested timeouts strictly decreasing.

### 4.4 Zero-Trust MCP, tool RBAC, HITL, PII, WORM

The planner (ReAct thought, P&E JSON, HuggingGPT task list, LLMCompiler DAG) is **untrusted**. OWASP LLM01: user **and retrieved** content can alter behavior. LLM06 Excessive Agency: least privilege, log, rate-limit. **PlanFlip**: planning-phase injection in tool outputs; one poisoned entry redirects all \(n\) sub-tasks; keyword filters can have **DR = 0.00**. MCP **tool descriptions** loaded at session init are an injection channel (Invariant Labs; cross-server shadowing; CVE-2025-54136 CVSS 8.8).

**Zero-Trust MCP:** remote servers are OAuth 2.1 resource servers. RFC 9728 metadata; **RFC 8707** `resource` indicator on auth and token requests; PKCE; MUST **validate audience**; MUST NOT **token-passthrough** (confused deputy). Gateway: terminate OAuth, **RFC 8693** exchange to upstream. Pin manifests `hash(description+schema)` against rug-pulls. Re-validate authorization **at execution**, not only at plan-approval (MCP rug-pull). DNS rebinding: pin TS SDK **≥1.24.0** / Python **≥1.23.0** (CVE-2025-66414 / 66416). Dual-LLM: quarantined model reads untrusted `tool_result`; privileged model holds tools.

**Tool RBAC:** never take identity from model JSON. Bind principal from session JWT / Temporal memo / MCP access token. Map `(principal, tenant, tool, args_shape)` → allow / deny / HITL. **Least privilege per plan node:** one named tool per P&E step. `allowed_tools` per hop so an observation cannot enable `shell`. Parse P&E plans as **schema-constrained JSON** (01/03); reject unknown tool names before the executor runs.

**HITL on irreversible tools:** LangGraph / Deep Agents `interrupt_on={"send_email": True, "issue_refund": True}`; OpenAI `AbortSignal`; Claude `permission_mode`; ADK `requested_tool_confirmations`. Show an **uneditable, sanitized** preview of name+args — do not let the model narrate “I will refund $X” without the actual args in the interrupt payload. Resume values must not concatenate into a new tool call without **re-RBAC**. Read-only search may stay autonomous. LLM06 mailbox: summarize-email agent with **send** + injected email → inbox exfil.

**PII pipeline (detect → redact → audit):** DLP at ingress **and** executor **before** checkpoint/trace. OpenAI Agents: `trace_include_sensitive_data` / env default **true** unless set false — if false, spans emit but **LLM I/O and tool args/outputs are stripped**. LangSmith / PostgresSaver store **full state**. Reflexion buffers and Self-Refine histories are **durable injection + PII**. Stable placeholders so cache prefixes survive. WORM: placeholder → **hash**, not plaintext.

**Immutable hop audit:** `correlation_id`, tenant, `thread_id`, hop, pattern (ReAct/P&E), `stop_reason`, frozen-goal hash, tool name, `call_id`, **hashed** args, policy/HITL decision, hash-circuit count, breaker state, `total_cost_usd` / `num_turns`, model id. Reconstruct a refund as: policy snapshot + frozen goal + sampled tool_use + hashed args + Stripe status + human interrupt. Kafka full log is a second copy. Provider traces are not a SIEM.

---

## 5. Production Enterprise Code

Assumptions match research: SDK `INITIAL_RETRY_DELAY=0.5`, `MAX_RETRY_DELAY=8.0`, `DEFAULT_MAX_RETRIES=2`; OpenAI `DEFAULT_MAX_TURNS=10`; hash circuit **N=3** / empty **N=6**; `max_replans=2`; critic **off** without oracle (Huang). Run offline: `python agent_loop.py`.

```python
#!/usr/bin/env python3
"""Agent-loop control plane. Python 3.11+.

  python agent_loop.py
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TypeVar

INITIAL_RETRY_DELAY = 0.5
MAX_RETRY_DELAY = 8.0
SDK_DEFAULT_MAX_RETRIES = 2
DEFAULT_MAX_TURNS = 10
HASH_CIRCUIT_N = 3
EMPTY_CALL_ABORT_N = 6
MAX_REPLANS = 2
CRITIC_M_CAP = 1  # open-ended; never Huang-style 5 rounds without oracle


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "msg": record.getMessage(),
            "logger": record.name,
            "correlation_id": getattr(record, "correlation_id", None),
            "tenant": getattr(record, "tenant", None),
            "thread_id": getattr(record, "thread_id", None),
            "hop": getattr(record, "hop", None),
            "pattern": getattr(record, "pattern", None),
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


def build_logger(
    correlation_id: str,
    tenant: str,
    thread_id: str | None = None,
    hop: int | None = None,
    pattern: str | None = None,
) -> CorrelationAdapter:
    base = logging.getLogger("agent.loop")
    if not base.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonLogFormatter())
        base.addHandler(handler)
        base.setLevel(logging.INFO)
        base.propagate = False
    extra: dict[str, Any] = {"correlation_id": correlation_id, "tenant": tenant}
    if thread_id:
        extra["thread_id"] = thread_id
    if hop is not None:
        extra["hop"] = hop
    if pattern:
        extra["pattern"] = pattern
    return CorrelationAdapter(base, extra)


class TransientError(Exception):
    def __init__(self, msg: str, retry_after: float | None = None, status: int | None = None) -> None:
        super().__init__(msg)
        self.retry_after = retry_after
        self.status = status


class PermanentError(Exception):
    pass


class CircuitOpenError(TransientError):
    pass


class MaxTurnsExceeded(PermanentError):
    pass


class BudgetExceeded(PermanentError):
    pass


class RemainingStepsGuard(PermanentError):
    """Soft fuse analogue of create_react_agent remaining_steps < 2."""


class HashCircuitTripped(PermanentError):
    pass


class BreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class LoopPattern(Enum):
    REACT = "react"
    PLAN_EXECUTE = "plan_execute"


class BreakerStateMachine:
    """Per (provider, model). Do not trip on 429-with-Retry-After."""

    def __init__(self, name: str, failure_threshold: int = 5, recovery_seconds: float = 30.0, half_open_max: int = 1) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self.half_open_max = half_open_max
        self._state = BreakerState.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._half_open_inflight = 0
        self._lock = asyncio.Lock()

    async def allow(self) -> None:
        async with self._lock:
            self._maybe_half_open()
            if self._state is BreakerState.OPEN:
                raise CircuitOpenError(f"circuit_open:{self.name}")
            if self._state is BreakerState.HALF_OPEN:
                if self._half_open_inflight >= self.half_open_max:
                    raise CircuitOpenError(f"circuit_half_open_busy:{self.name}")
                self._half_open_inflight += 1

    def _maybe_half_open(self) -> None:
        if self._state is BreakerState.OPEN and (time.monotonic() - self._opened_at) >= self.recovery_seconds:
            self._state = BreakerState.HALF_OPEN
            self._half_open_inflight = 0

    async def record_success(self) -> None:
        async with self._lock:
            self._failures = 0
            self._half_open_inflight = 0
            self._state = BreakerState.CLOSED

    async def record_failure(self, *, trip: bool = True) -> None:
        async with self._lock:
            if not trip:
                return
            self._failures += 1
            if self._state is BreakerState.HALF_OPEN or self._failures >= self.failure_threshold:
                self._state = BreakerState.OPEN
                self._opened_at = time.monotonic()
                self._half_open_inflight = 0

    @property
    def state(self) -> BreakerState:
        return self._state


T = TypeVar("T")


async def retry_with_jitter(
    fn: Callable[[], Awaitable[T]],
    *,
    log: CorrelationAdapter,
    attempts: int = SDK_DEFAULT_MAX_RETRIES + 1,
    base: float = INITIAL_RETRY_DELAY,
    cap: float = MAX_RETRY_DELAY,
) -> T:
    """HTTP/transport loop ONLY. Full jitter. Never wrap hop-fuse exceptions."""
    last: Exception | None = None
    for i in range(attempts):
        try:
            return await fn()
        except PermanentError:
            raise
        except TransientError as exc:
            last = exc
            if i == attempts - 1:
                break
            ra = exc.retry_after
            sleep_s = ra if ra is not None and 0 < ra <= 60 else random.random() * min(cap, base * (2**i))
            log.warning("http_retry attempt=%s sleep_s=%.3f err=%s", i + 1, sleep_s, exc)
            await asyncio.sleep(sleep_s)
    assert last is not None
    raise last


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def args_hash(tool: str, args: dict[str, Any]) -> str:
    material = f"{tool}|{canonical_json(args)}"
    return hashlib.sha256(material.encode()).hexdigest()[:16]


@dataclass
class HopBudget:
    """Three non-portable clocks. remaining_steps is LangGraph-style super-steps left."""

    max_turns: int = DEFAULT_MAX_TURNS
    recursion_limit: int = 25
    max_budget_usd: float = 0.25
    hops: int = 0
    super_steps: int = 0
    spend_usd: float = 0.0
    last_call_cost: float = 0.012  # [inferred] toy hop; pad Claude budget by one call

    @property
    def remaining_steps(self) -> int:
        return max(0, self.recursion_limit - self.super_steps)

    def charge_model_call(self) -> None:
        self.hops += 1
        self.super_steps += 1
        self.spend_usd += self.last_call_cost

    def charge_tool_superstep(self) -> None:
        self.super_steps += 1

    def assert_may_call_model(self) -> None:
        if self.hops >= self.max_turns:
            raise MaxTurnsExceeded(f"max_turns={self.max_turns}")
        if self.spend_usd + self.last_call_cost > self.max_budget_usd:
            raise BudgetExceeded(f"max_budget_usd={self.max_budget_usd} spent={self.spend_usd:.4f}")

    def assert_tool_round_fits(self) -> None:
        if self.remaining_steps < 2:
            raise RemainingStepsGuard("remaining_steps<2; wrap up, do not dispatch tools")


@dataclass
class HashCircuit:
    """Poison repeating (tool, canonical_args). N=3 failures → hard-stop that tool."""

    n: int = HASH_CIRCUIT_N
    empty_abort: int = EMPTY_CALL_ABORT_N
    _fail: dict[str, int] = field(default_factory=dict)
    _empty: int = 0
    blocked: set[str] = field(default_factory=set)

    def record_empty(self) -> None:
        self._empty += 1
        if self._empty >= self.empty_abort:
            raise HashCircuitTripped("empty_tool_calls>=6; abort stream")

    def record_result(self, tool: str, args: dict[str, Any], is_error: bool) -> int:
        key = args_hash(tool, args)
        if not is_error:
            self._fail[key] = 0
            self._empty = 0
            return 0
        self._fail[key] = self._fail.get(key, 0) + 1
        if self._fail[key] >= self.n:
            self.blocked.add(tool)
        return self._fail[key]


@dataclass
class Checkpoint:
    thread_id: str
    hop: int
    pattern: str
    messages: list[dict[str, Any]]
    plan: list[str]
    past_steps: list[tuple[str, str]]
    frozen_goal: str


class Checkpointer:
    """LangGraph-like: snapshot at super-step boundaries. No thread_id ⇒ no save."""

    def __init__(self) -> None:
        self._rows: dict[str, Checkpoint] = {}

    def save(self, cp: Checkpoint) -> None:
        if not cp.thread_id:
            return
        self._rows[cp.thread_id] = Checkpoint(
            thread_id=cp.thread_id,
            hop=cp.hop,
            pattern=cp.pattern,
            messages=list(cp.messages),
            plan=list(cp.plan),
            past_steps=list(cp.past_steps),
            frozen_goal=cp.frozen_goal,
        )

    def load(self, thread_id: str) -> Checkpoint | None:
        return self._rows.get(thread_id)


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]


@dataclass
class ModelTurn:
    text: str
    tool_calls: list[ToolCall]
    cost_usd: float = 0.012


@dataclass
class ToolObservation:
    name: str
    is_error: bool
    content: str


class Critic:
    """Optional. Without an oracle, Huang shows accuracy *drops*. Never silent."""

    def __init__(self, *, enabled: bool, has_oracle: bool, log: CorrelationAdapter) -> None:
        self.enabled = enabled
        self.has_oracle = has_oracle
        self.log = log
        self.rounds = 0

    def may_reflect(self) -> bool:
        if not self.enabled:
            return False
        if not self.has_oracle:
            self.log.warning(
                "critic_disabled_no_oracle Huang trap: GPT-4 GSM8K 95.5→89.0 after 5 oracle-free rounds"
            )
            return False
        if self.rounds >= CRITIC_M_CAP:
            return False
        return True

    def reflect(self, trial_text: str, oracle_ok: bool) -> str | None:
        if not self.may_reflect():
            return None
        if oracle_ok:
            return None
        self.rounds += 1
        return f"verbal_hint: previous trial failed; do not repeat args. excerpt={trial_text[:80]}"


class FakeModel:
    def __init__(self, script: list[ModelTurn]) -> None:
        self.script = list(script)
        self.i = 0

    async def invoke(self) -> ModelTurn:
        if self.i >= len(self.script):
            return ModelTurn(text="final", tool_calls=[])
        turn = self.script[self.i]
        self.i += 1
        return turn


class FakeTools:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail_tools: set[str] = set()
        self.fail_remaining: dict[str, int] = {}

    async def execute(self, call: ToolCall) -> ToolObservation:
        self.calls.append((call.name, dict(call.arguments)))
        if not call.arguments:
            return ToolObservation(call.name, True, "empty_input")
        left = self.fail_remaining.get(call.name, 0)
        if left > 0:
            self.fail_remaining[call.name] = left - 1
            return ToolObservation(call.name, True, "downstream_5xx")
        if call.name in self.fail_tools:
            return ToolObservation(call.name, True, "downstream_5xx")
        return ToolObservation(call.name, False, f"ok:{call.name}:{canonical_json(call.arguments)}")


class LoopState:
    def __init__(self, frozen_goal: str, thread_id: str) -> None:
        self.frozen_goal = frozen_goal
        self.thread_id = thread_id
        self.messages: list[dict[str, Any]] = [{"role": "user", "content": frozen_goal}]
        self.plan: list[str] = []
        self.past_steps: list[tuple[str, str]] = []
        self.replans = 0
        self.stop_reason = "running"
        self.final_text = ""


def deterministic_degraded(goal: str) -> str:
    return json.dumps({"status": "degraded", "goal": goal, "answer": None}, separators=(",", ":"))


class FallbackChain:
    def __init__(
        self,
        primary: Callable[[], Awaitable[ModelTurn]],
        secondary: Callable[[], Awaitable[ModelTurn]],
        breaker: BreakerStateMachine,
    ) -> None:
        self.primary = primary
        self.secondary = secondary
        self.breaker = breaker

    async def invoke(self, log: CorrelationAdapter) -> ModelTurn:
        try:
            await self.breaker.allow()
            result = await retry_with_jitter(self.primary, log=log)
            await self.breaker.record_success()
            return result
        except CircuitOpenError as exc:
            log.warning("llm_breaker_open err=%s", exc)
        except TransientError as exc:
            await self.breaker.record_failure(trip=True)
            log.warning("llm_primary_transient err=%s", exc)
        except PermanentError as exc:
            await self.breaker.record_failure(trip=False)
            log.error("llm_permanent_no_failover err=%s", exc)
            raise
        try:
            return await retry_with_jitter(self.secondary, log=log)
        except (TransientError, PermanentError) as exc:
            log.error("degraded_deterministic err=%s", exc)
            raise PermanentError("degraded") from exc


def build_controller(
    log: CorrelationAdapter,
    *,
    pattern: LoopPattern = LoopPattern.REACT,
    script: list[ModelTurn] | None = None,
    model: FakeModel | None = None,
    tools: FakeTools | None = None,
    budget: HopBudget | None = None,
    checkpointer: Checkpointer | None = None,
    critic: Critic | None = None,
    allowed: frozenset[str] | None = None,
    irreversible: frozenset[str] | None = None,
    max_turns: int = DEFAULT_MAX_TURNS,
    recursion_limit: int = 50,
    max_budget_usd: float = 5.0,
    max_replans: int = MAX_REPLANS,
    fallback: FallbackChain | None = None,
) -> LoopController:
    return LoopController(
        pattern,
        model or FakeModel(script or []),
        tools or FakeTools(),
        budget or HopBudget(max_turns=max_turns, recursion_limit=recursion_limit, max_budget_usd=max_budget_usd),
        checkpointer or Checkpointer(),
        critic or Critic(enabled=False, has_oracle=False, log=log),
        log,
        allowed or frozenset(),
        irreversible or frozenset(),
        max_replans=max_replans,
        fallback=fallback,
    )


class LoopController:
    """ReAct vs P&E dispatcher. Control plane owns fuses; model never terminates the process."""

    def __init__(
        self,
        pattern: LoopPattern,
        model: FakeModel,
        tools: FakeTools,
        budget: HopBudget,
        checkpointer: Checkpointer,
        critic: Critic,
        log: CorrelationAdapter,
        allowed_tools: frozenset[str],
        irreversible: frozenset[str],
        max_replans: int = MAX_REPLANS,
        fallback: FallbackChain | None = None,
    ) -> None:
        self.pattern = pattern
        self.model = model
        self.tools = tools
        self.budget = budget
        self.checkpointer = checkpointer
        self.critic = critic
        self.log = log
        self.allowed_tools = allowed_tools
        self.irreversible = irreversible
        self.max_replans = max_replans
        self.fallback = fallback
        self.hash_circuit = HashCircuit()
        self.hitl_pending: list[ToolCall] = []

    async def _model_turn(self) -> ModelTurn:
        self.budget.assert_may_call_model()
        if self.fallback is not None:
            turn = await self.fallback.invoke(self.log)
        else:
            turn = await retry_with_jitter(self.model.invoke, log=self.log)
        self.budget.last_call_cost = turn.cost_usd
        self.budget.charge_model_call()
        return turn

    async def _run_tools(self, calls: list[ToolCall], state: LoopState) -> list[ToolObservation]:
        self.budget.assert_tool_round_fits()
        self.budget.charge_tool_superstep()
        obs: list[ToolObservation] = []
        for call in calls:
            if not call.arguments:
                self.hash_circuit.record_empty()
            if call.name not in self.allowed_tools or call.name in self.hash_circuit.blocked:
                o = ToolObservation(call.name, True, "rbac_or_circuit_blocked")
                obs.append(o)
                state.messages.append({"role": "tool", "name": call.name, "content": o.content})
                continue
            if call.name in self.irreversible:
                self.hitl_pending.append(call)
                o = ToolObservation(call.name, True, "hitl_required")
                obs.append(o)
                state.messages.append({"role": "tool", "name": call.name, "content": o.content})
                continue
            o = await self.tools.execute(call)
            n = self.hash_circuit.record_result(call.name, call.arguments, o.is_error)
            if n >= HASH_CIRCUIT_N:
                o = ToolObservation(
                    call.name,
                    True,
                    f"this tool failed identically {n} times; do not retry; answer or replan",
                )
            obs.append(o)
            state.messages.append({"role": "tool", "name": call.name, "content": o.content})
        return obs

    def _persist(self, state: LoopState) -> None:
        self.checkpointer.save(
            Checkpoint(
                thread_id=state.thread_id,
                hop=self.budget.hops,
                pattern=self.pattern.value,
                messages=state.messages,
                plan=state.plan,
                past_steps=state.past_steps,
                frozen_goal=state.frozen_goal,
            )
        )

    async def run_react(self, state: LoopState) -> LoopState:
        while True:
            try:
                turn = await self._model_turn()
            except (MaxTurnsExceeded, BudgetExceeded) as exc:
                state.stop_reason = type(exc).__name__
                state.final_text = state.final_text or "partial: fuse tripped"
                self._persist(state)
                return state
            except RemainingStepsGuard:
                state.stop_reason = "RemainingStepsGuard"
                state.final_text = "Sorry, need more steps to process this request."
                self._persist(state)
                return state
            if not turn.tool_calls:
                state.final_text = turn.text
                if self.critic.enabled:
                    self.critic.may_reflect()  # Huang warning if no oracle; no extra trial on success
                state.stop_reason = "end_turn"
                self._persist(state)
                return state
            try:
                await self._run_tools(turn.tool_calls, state)
            except RemainingStepsGuard:
                state.stop_reason = "RemainingStepsGuard"
                state.final_text = "Sorry, need more steps to process this request."
                self._persist(state)
                return state
            except HashCircuitTripped as exc:
                state.stop_reason = "HashCircuitTripped"
                state.final_text = str(exc)
                self._persist(state)
                return state
            self._persist(state)

    async def run_plan_execute(self, state: LoopState) -> LoopState:
        try:
            plan_turn = await self._model_turn()
        except (MaxTurnsExceeded, BudgetExceeded) as exc:
            state.stop_reason = type(exc).__name__
            state.final_text = deterministic_degraded(state.frozen_goal)
            return state
        state.plan = [plan_turn.text] if not plan_turn.tool_calls else [c.name for c in plan_turn.tool_calls]
        if plan_turn.tool_calls:
            # Planner emitted a DAG/list; workers run without planner seeing obs (ReWOO).
            try:
                obs = await self._run_tools(plan_turn.tool_calls, state)
            except RemainingStepsGuard:
                state.stop_reason = "RemainingStepsGuard"
                state.final_text = "Sorry, need more steps to process this request."
                self._persist(state)
                return state
            except HashCircuitTripped as exc:
                state.stop_reason = "HashCircuitTripped"
                state.final_text = str(exc)
                self._persist(state)
                return state
            for call, o in zip(plan_turn.tool_calls, obs, strict=True):
                state.past_steps.append((call.name, o.content))
            if any(o.is_error for o in obs) and state.replans < self.max_replans:
                state.replans += 1
                self.log.info("replan n=%s frozen_goal=%s", state.replans, state.frozen_goal)
                return await self.run_plan_execute(state)
        try:
            solver = await self._model_turn()
        except (MaxTurnsExceeded, BudgetExceeded):
            state.stop_reason = "fuse_after_workers"
            state.final_text = "partial: workers done, solver fuse"
            self._persist(state)
            return state
        state.final_text = solver.text
        state.stop_reason = "end_turn"
        self._persist(state)
        return state

    async def run(self, frozen_goal: str, thread_id: str) -> LoopState:
        existing = self.checkpointer.load(thread_id)
        state = LoopState(frozen_goal, thread_id)
        if existing is not None:
            state.messages = list(existing.messages)
            state.plan = list(existing.plan)
            state.past_steps = list(existing.past_steps)
            state.frozen_goal = existing.frozen_goal  # never let observations rewrite
        self.log.info("loop_start pattern=%s goal_hash=%s", self.pattern.value, hashlib.sha256(frozen_goal.encode()).hexdigest()[:8])
        if self.pattern is LoopPattern.REACT:
            return await self.run_react(state)
        return await self.run_plan_execute(state)


async def _offline() -> None:
    cid = str(uuid.uuid4())
    tenant = "acme"
    log = build_logger(cid, tenant, thread_id="acme:ticket-1", hop=0, pattern="react")
    slept: list[float] = []
    real_sleep = asyncio.sleep

    async def _sleep(s: float) -> None:
        slept.append(s)

    asyncio.sleep = _sleep  # type: ignore[method-assign]
    try:
        async def once() -> int:
            raise TransientError("429", retry_after=0.4)
        try:
            await retry_with_jitter(once, log=log, attempts=2)
        except TransientError:
            pass
        assert slept and abs(slept[0] - 0.4) < 1e-9
    finally:
        asyncio.sleep = real_sleep  # type: ignore[method-assign]

    br = BreakerStateMachine("llm:sol", failure_threshold=1, recovery_seconds=0.05)

    async def boom() -> ModelTurn:
        raise TransientError("529")

    try:
        await FallbackChain(boom, boom, br).invoke(log)
        raise AssertionError("expected degraded")
    except PermanentError:
        pass
    assert br.state is BreakerState.OPEN
    await asyncio.sleep(0.06)
    try:
        await br.allow()
    except CircuitOpenError:
        raise AssertionError("should be half-open") from None
    await br.record_success()
    assert br.state is BreakerState.CLOSED
    assert json.loads(deterministic_degraded("g"))["status"] == "degraded"

    budget = HopBudget(max_turns=10, recursion_limit=50, max_budget_usd=5.0)
    ckpt = Checkpointer()
    ctrl = build_controller(
        log,
        script=[ModelTurn("search", [ToolCall("search_kb", {"q": "refund"})]) for _ in range(12)],
        budget=budget,
        checkpointer=ckpt,
        critic=Critic(enabled=True, has_oracle=False, log=log),
        allowed=frozenset({"search_kb", "lookup_policy"}),
        irreversible=frozenset({"issue_refund", "send_email"}),
    )
    st = await ctrl.run("refund policy for ticket-1", "acme:ticket-1")
    assert st.stop_reason == "MaxTurnsExceeded" and budget.hops == 10
    loaded = ckpt.load("acme:ticket-1")
    assert loaded is not None and loaded.hop == 10 and loaded.frozen_goal.startswith("refund")

    tight = HopBudget(max_turns=20, recursion_limit=3, max_budget_usd=5.0)
    tight.super_steps = 2
    st2 = await build_controller(
        log, script=[ModelTurn("need tool", [ToolCall("search_kb", {"q": "x"})])],
        budget=tight, allowed=frozenset({"search_kb"}),
    ).run("q", "acme:t2")
    assert st2.stop_reason == "RemainingStepsGuard" and "need more steps" in st2.final_text

    fail_tools = FakeTools()
    fail_tools.fail_tools.add("search_kb")
    same = ToolCall("search_kb", {"q": "same"})
    ctrl3 = build_controller(
        log,
        script=[ModelTurn("t", [same]), ModelTurn("t", [same]), ModelTurn("t", [same]), ModelTurn("give up", [])],
        tools=fail_tools, allowed=frozenset({"search_kb"}),
    )
    st3 = await ctrl3.run("q", "acme:t3")
    assert st3.stop_reason == "end_turn" and "search_kb" in ctrl3.hash_circuit.blocked
    assert any("failed identically" in m.get("content", "") for m in st3.messages if m.get("role") == "tool")

    warn_log = build_logger(cid, tenant, thread_id="acme:t4", pattern="react")
    c_bad = Critic(enabled=True, has_oracle=False, log=warn_log)
    assert c_bad.may_reflect() is False and c_bad.reflect("ans", oracle_ok=False) is None
    c_ok = Critic(enabled=True, has_oracle=True, log=warn_log)
    assert c_ok.reflect("wrong", oracle_ok=False) is not None
    assert c_ok.reflect("wrong", oracle_ok=False) is None

    pe_tools = FakeTools()
    pe_tools.fail_remaining["web_search"] = 2
    pe_log = build_logger(cid, tenant, thread_id="acme:research-1", pattern="plan_execute")
    st_pe = await build_controller(
        pe_log, pattern=LoopPattern.PLAN_EXECUTE, tools=pe_tools,
        script=[
            ModelTurn("plan", [ToolCall("web_search", {"q": "vendor-a"}), ToolCall("web_search", {"q": "vendor-b"})]),
            ModelTurn("replan", [ToolCall("web_search", {"q": "vendor-c"}), ToolCall("lookup_policy", {"id": "p1"})]),
            ModelTurn("synthesis", []),
        ],
        allowed=frozenset({"web_search", "lookup_policy"}), irreversible=frozenset({"send_email"}),
    ).run("batch research vendors", "acme:research-1")
    assert st_pe.stop_reason == "end_turn" and st_pe.replans == 1
    assert st_pe.frozen_goal == "batch research vendors" and st_pe.final_text == "synthesis"

    pe_cap_tools = FakeTools()
    pe_cap_tools.fail_tools.add("web_search")
    st_cap = await build_controller(
        log, pattern=LoopPattern.PLAN_EXECUTE, tools=pe_cap_tools,
        script=[
            ModelTurn("p0", [ToolCall("web_search", {"q": "x"})]),
            ModelTurn("p1", [ToolCall("web_search", {"q": "y"})]),
            ModelTurn("p2", [ToolCall("web_search", {"q": "z"})]),
            ModelTurn("solver-anyway", []),
        ],
        allowed=frozenset({"web_search"}),
    ).run("research", "acme:research-cap")
    assert st_cap.replans == 2 and st_cap.stop_reason == "end_turn" and st_cap.final_text == "solver-anyway"

    hitl_ctrl = build_controller(
        log,
        script=[ModelTurn("refund", [ToolCall("issue_refund", {"cents": 500})]), ModelTurn("wait", [])],
        allowed=frozenset({"issue_refund"}), irreversible=frozenset({"issue_refund"}),
        max_turns=5, recursion_limit=20, max_budget_usd=1.0,
    )
    st_h = await hitl_ctrl.run("refund 5", "acme:pay-1")
    assert hitl_ctrl.hitl_pending[0].name == "issue_refund" and st_h.stop_reason == "end_turn"

    print(json.dumps({
        "ok": True, "cid": cid, "react_stop": st.stop_reason, "react_hops": budget.hops,
        "remaining_steps_stop": st2.stop_reason, "hash_blocked": sorted(ctrl3.hash_circuit.blocked),
        "pe_stop": st_pe.stop_reason, "pe_replans": st_pe.replans, "pe_cap_replans": st_cap.replans,
        "breaker": br.state.value, "hitl": len(hitl_ctrl.hitl_pending),
        "degraded": json.loads(deterministic_degraded("g"))["status"],
    }, indent=2))


if __name__ == "__main__":
    asyncio.run(_offline())
```

**Behavior encoded (maps to §§1–4):**

- Full-jitter **HTTP** retries; `Retry-After` honored iff \(0 < t \leq 60\); hop-fuse exceptions are **PermanentError** (no sleep).
- Breaker closed → open → half-open; 429-with-RA does not trip; probe after recovery timer; fallback primary → secondary → `status: "degraded"`. **PermanentError does not failover.**
- JSON logs carry `correlation_id` + tenant + `thread_id` + hop + pattern.
- **Hop cap** `max_turns=10` → `MaxTurnsExceeded` (not infinite ReAct).
- **`remaining_steps < 2`** wrap-up: “Sorry, need more steps…” — no further tool dispatch.
- **Identical-call circuit**: 3 same `(tool, args)` failures → hard observation + block that tool.
- **Critic optional**: no oracle → Huang warning, **zero** extra trials; oracle + M cap **1**.
- **ReAct vs P&E dispatcher**: P&E workers run without planner seeing obs; `max_replans=2`; frozen goal never rewritten from observations.
- Checkpointer keyed by `thread_id`; HITL on irreversible tools (no auto `issue_refund`).

**Interview talking point:** retries with jitter handle 529; they do not cap a ReAct search loop. Hop budget + hash circuit + remaining-steps wrap-up are three different classes.

---

## 6. Architectural System Design Scenarios

Exactly two enterprise designs. Numbers from the research file; scale-up arithmetic marked **[inferred]**.

### Scenario 1 — Customer-support ReAct with a 10-turn cap

**Problem statement.** Multi-tenant support copilot: **1k conversations/day**, peak **100 concurrent** tickets. Job is **1–4 hops**: retrieve ticket + policy + optional mutation (refund, email). Latency SLO is **seconds**, not research hours. Mix **[inferred]**: 70% 1-hop **$24/1k**, 25% 3-hop **$38/1k**, 5% 10-hop **$85/1k** → **~$31/day** model + **$2** search SaaS. A 25-hop runaway fleet is **~$187/day (~6×)** and an incident review if one refunded twice. Policy must **not** drift from ticket-body injection. Constraint: OpenAI `DEFAULT_MAX_TURNS=10` is enough for lookup+refund decision and **not** for “refactor auth”; HITL on `issue_refund` / `send_email`; `trace_include_sensitive_data=false`.

**Proposed architecture.**

```
                    ┌──────────────────────────────────────────────────────────┐
                    │ EDGE  auth, tenant TPM, correlation-id, PII redact       │
                    │ thread_id = tenant:ticket_id  (never a shared constant)  │
                    └────────────────────────────┬─────────────────────────────┘
                                                 │
                    ┌────────────────────────────▼─────────────────────────────┐
                    │ CONTROL  Temporal workflow = tenant:ticket               │
                    │  LOOP CONTROLLER = ReAct (create_agent / Runner)         │
                    │  HOP COUNTER: max_turns=8–10; remaining_steps wrap-up    │
                    │               max_budget_usd=$0.25/ticket; hash N=3      │
                    │  CHECKPOINTER sync around refund HITL                    │
                    │  CRITIC off (no oracle on chat; Huang trap)              │
                    │  Activity LLM: SDK max_retries=0; parallel reads OK      │
                    │  Activity tools: get_ticket, search_policy,              │
                    │    issue_refund (HITL), send_email (HITL)                │
                    │  CircuitBreaker(llm) ≠ CircuitBreaker(payments)          │
                    └─────┬───────────────────────────────┬────────────────────┘
                          │                               │
                          ▼                               ▼
                    ┌──────────────────┐            ┌─────────────────────────┐
                    │ DATA  Generation │            │ DATA  Executor          │
                    │ cached prefix    │            │ Stripe same key after   │
                    │ tools[] frozen   │            │ 500; KB search cap page │
                    └────────┬─────────┘            └──────────┬──────────────┘
                             │                                 │
                    ┌────────▼─────────┐            ┌──────────▼──────────────┐
                    │ TOOL PROXIES     │            │ PERSIST  ckpt + WORM    │
                    │ ticket body =    │            │ hop, stop_reason,       │
                    │ untrusted obs    │            │ hashed args, HITL       │
                    │ no shell / MCP   │            │ Kafka: Stripe → Signal  │
                    └──────────────────┘            └─────────────────────────┘
```

**Technology choices.** Interleaved ReAct via `create_agent` or OpenAI `Runner` — **not** 12-trial Reflexion, **not** LATS. Parallel `lookup_customer` + `lookup_policy` in **one** batched-action hop if independent (03). Prefix-stable tools array (02). Stripe idempotency from `tool_use_id` (03). Eval success = correct policy citation + correct refund amount, **not** “the loop ended.” Instrument `n_turns`, `cached_input_tokens`, `tool_repeat_count`, HITL wait, `MaxTurnsExceeded` rate. A 1% infinite-loop rate at 1k tickets is **10** runaway traces; at 25 hops **[inferred] ~$1.9 extra/day** on sol — small money, large incident if one double-refunded.

**Trade-off evaluation matrix.**

| Dimension | A. `max_turns=None`, no hash circuit, Reflexion-on-every-ticket | B. Recommended: ReAct `max_turns=8–10`, remaining_steps wrap-up, hash N=3, critic off, HITL money | C. Outer P&E DAG for every ticket (plan 8 steps even for “what is my order status”) |
| --- | --- | --- | --- |
| **Cost / 1k** | Mix **$31/day** → Reflexion 9-call **+$108/day** if on everyone; 25-hop runaway **$187/day** | Mix **[inferred] $31/day** + $2 search; fuse stops the **$187** incident | Planner tokens on **every** 1-hop ticket; ReWOO win only when independent fan-out exists |
| **Latency** | 12 AlfWorld-style trials; LATS-class minutes | p50 **1–3 hops / <8 s** **[inferred policy]**; p99 = HITL SLA | Planner **1.88 s** leftover even when tools are fast (LLMCompiler Movie Rec) |
| **Ops complexity** | Looks simple until 51 min empty `{}` | Medium (Temporal + two breakers + HITL + three clocks) | Extra planner/replanner nodes for a 1-tool lookup |
| **Security posture** | Ticket injection can loop tools forever; critic without oracle **edits correct answers** | Frozen goal; allowlist 4 tools; HITL irreversible; traces stripped | Plan JSON is another injection surface; still need hop cap on the inner executor |
| **Scalability ceiling** | 100 agents × unbounded hops = TPM + Stripe incident | 100 × 10 turns bounded; bulkhead payments vs KB | Planner serial remainder dominates when tools are cheap |

**Decision rationale.** **B** is the only design that treats `max_turns=10` as a **financial control** matching the paper’s finding that extra HotpotQA steps recover **0.84%** of correct trajectories — support tickets do not need ALFWorld’s 49-step fuse. A fails the money exam (Reflexion 12 trials, Huang, unbounded hops). C pays LLMCompiler’s planner overhead on a path whose next tool **must** depend on the last observation (troubleshooting). Quote: cached 10-hop **$85/1k** vs 25-hop **$187/1k** vs mix **$31/day**.

### Scenario 2 — Batch plan-and-execute research with a replan cap

**Problem statement.** Overnight / hours-OK research jobs: multi-hop web research, **8+ independent** searches, one synthesis. Failure mode is **cost and loop**, not 800 ms p95. LLMCompiler showed interleaved ReAct at **20k** input tokens vs Compiler **2.8k** on Movie Rec (**~7.1×** fewer in) and **6.73×** cheaper; ~**10%** of HotpotQA ReAct **looped**. Constraint: `max_replans=2`; inner executor `max_turns=3` + **single-tool allowlist** per plan node (PlanFlip blast radius); parent `recursion_limit` 200–1000 **and** worker subagents get their **own** limit (Deep Agents #1698); `max_budget_usd` **$2–5**/job; web-search count cap ($10/1k — 01); HITL before any **write** or **send**. Optional CRITIC **with search** on the final answer — not Self-Refine-only. Reflexion **only** if a grader exists and trials ≤2.

**Proposed architecture.**

```
  ┌─────────────┐    ┌─────────────────────────────────────────────────────────┐
  │ Batch job / │───▶│ CONTROL  Temporal: one Workflow per research job        │
  │ analyst UI  │    │  LOOP CONTROLLER = P&E (LLMCompiler DAG / ReWOO)        │
  │             │    │  PLAN (schema-constrained JSON, tool enum)              │
  │             │    │  FETCH-READY ⇉ worker Activities (Haiku / luna)         │
  │             │    │    each worker: inner ReAct max_turns=3, 1-tool ACL     │
  │             │    │  JOIN / REPLAN cap 2 (empty evidence only)              │
  │             │    │  SOLVER (Sonnet / GPT-6-sol) + optional CRITIC+search   │
  │             │    │  HOP COUNTER: max_tool_calls + max_budget_usd $2–5      │
  │             │    │  HASH CIRCUIT per worker; parent limit ≠ subagent limit │
  │             │    │  CHECKPOINTER: plan + past_steps + blob snippets        │
  │             │    │  CRITIC: on iff search/oracle; Huang warning otherwise  │
  └─────────────┘    └───────────┬───────────────────────────┬─────────────────┘
                                 │                           │
                                 ▼                           ▼
                     ┌─────────────────────┐     ┌─────────────────────────────┐
                     │ DATA  Generation    │     │ TOOL PROXIES  web_search    │
                     │ planner prefix      │     │ snippets = PlanFlip class   │
                     │ cached (02)         │     │ untrusted; XML delimit (02) │
                     │ workers small model │     │ HITL before send/write      │
                     └──────────┬──────────┘     └──────────────┬──────────────┘
                                ▼                               ▼
                     ┌─────────────────────────────────────────────────────────┐
                     │ PERSIST  plan object + worker blobs + WORM (cid, hop,   │
                     │          replan n, hashed queries, frozen_goal hash)    │
                     └─────────────────────────────────────────────────────────┘
```

**Technology choices.** Do **not** interleaved-ReAct the planner. HuggingGPT-like `dep` placeholders if tools are heterogeneous. Persist the **plan object**; Temporal: one Activity per worker; Continue-as-needed so history does not hold 500 KB snippets (03). Crash mid-worker: pending writes / Activity retry, **not** a full replan. When **not** to use P&E: live incident troubleshooting where the next tool cannot be foreseen — then ReAct with a tight hop cap (Scenario 1). Hybrid that ships: outer P&E (5–15 steps, DAG where possible) + **inner** ReAct per step.

**Trade-off evaluation matrix.**

| Dimension | A. Interleaved ReAct on the planner (20k-token Movie Rec shape) | B. Recommended: P&E/DAG + inner ReAct `max_turns=3` + `max_replans=2` + worker fuses | C. LATS / MCTS / 12-trial Reflexion on the customer-facing batch queue |
| --- | --- | --- | --- |
| **Cost / 1k** | ReWOO **5×** tokens; Compiler Movie Rec ReAct **$20.46/1k** vs **$3.04/1k** (2023); quadratic \(C+S\) | Structural **3.37–6.73×** cheaper; Haiku workers; inner cap bounds poisoned hops | LATS **30 trajectories**; Reflexion **[inferred] $1.02/task** × fleet |
| **Latency** | ReAct† Movie Rec **20.47 s** mean; loops add unbounded p99 | Compiler **5.47 s** mean; streaming planner up to **1.3×**; hours-OK SLO | WebShop LATS **1,066 s** vs Compiler **~11 s** (**101.7×**) |
| **Ops complexity** | One graph until 10% HotpotQA-style divergence | Medium (DAG fetch unit, per-worker limits, blob store, two fuses) | Search-tree ops on a batch queue |
| **Security posture** | One injected snippet can steer **all** subsequent TAO hops | Frozen goal; plan schema enum; inner allowlist = 1 tool; PlanFlip blast radius **3** hops | More samples = more injection surface; still needs a checker |
| **Scalability ceiling** | Prefix+observations quadratic; Deep Agents subagent **25** looks like model failure | Worker pool × search RPM; parent 1000 irrelevant unless propagated | CPU/time budget of MCTS, not TPM |

**Decision rationale.** **B** is the only design that keeps the **planner prefix off the per-search hot path** (the ReWOO/LLMCompiler result) while bounding **goal hijack** to an inner 3-turn worker (PlanFlip). A is the 20k-input interview fail. C is a research spend pattern — LLMCompiler already beat LATS on WebShop wall-clock at similar score. Quote: **do not re-send the planner prefix every search**; cap Joiner replans at **2**.

---

## Key numbers to memorize

| Number | What |
| --- | --- |
| **$24 / $38 / $85 / $187 per 1k** | Cached sol 1 / 3 / 10 / 25 hops **[inferred]** |
| **~$31/day vs ~$187/day** | 1k-ticket mix vs 25-hop runaway fleet **[inferred]** |
| **$108/1k vs $12/1k** | Self-Refine ≤9 calls vs one-shot (math ~0 pp) **[inferred]** |
| **$19.59 vs $3.97 per 1k queries** | ReWOO HotpotQA ReAct vs batched (gpt-3.5, 2023) |
| **3.37× / 6.73× cheaper** | LLMCompiler vs ReAct† HotpotQA / Movie Rec |
| **1.80× / 3.74× faster** | same benches (mean latency) |
| **20k vs 2.8k** | Movie Rec ReAct vs Compiler input tokens |
| **47% / 23% / 0%** | ReAct fail: repetitive TAO / empty search / hallucination |
| **0.84% / 1.33%** | Correct ReAct traj. that needed full 7 / 5 steps |
| **10 / None / 25→1000** | OpenAI max_turns / Claude max_turns default / LangGraph recursion_limit |
| **2 super-steps** | ReAct model+tools per tool round |
| **N=3 / N=6** | Identical `(tool,args)` fail circuit / empty-call abort |
| **2–3** | `max_replans`; Self-Refine **M≤4** but production M=1 without oracle |
| **95.5 → 89.0** | Huang GPT-4 GSM8K after 5 oracle-free calls |
| **91.0 vs 80.1 / 52 vs 60** | Reflexion HumanEval; Rust ablation **without tests** |
| **12 / 4** | AlfWorld Reflexion trials / WebShop died |
| **1,066 s vs ~11 s** | LATS vs LLMCompiler WebShop |
| **600 s / 300 s** | Claude async stall / stream idle watchdog |
| **$0.25 / $2–5** | Support ticket vs research job `max_budget_usd` examples |

**Interview closer:** “The model is an untrusted planner. I dispatch ReAct when the next tool must see the last observation, and plan-and-execute when work is independent — with `max_turns`, `recursion_limit`, and `max_budget_usd` as three different clocks, a hash circuit on identical `(tool, args)`, remaining-steps wrap-up before the fuse, and no critic unless I have an oracle. Huang paid 5× to lose 6.5 points; I will not.”
