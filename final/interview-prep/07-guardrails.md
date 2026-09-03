# Guardrails (Runtime PDP / Sandbox / Egress)

## What Is This?

Imagine a bowling alley with bumper rails. The bowler (your LLM) still throws the ball, but the rails prevent it from going into the gutter. Guardrails for AI systems work the same way: they are safety filters that sit around LLM calls and agent actions to enforce policy, block harmful outputs, protect sensitive data, and ensure response quality. They are not a single product or wrapper -- they are a layered defense architecture spanning input validation, output filtering, behavioral policy, and runtime observability.

An LLM predicts the next token. It does **not** enforce a data/instruction boundary. UK NCSC (8 Dec 2025): there is no parameterized-query equivalent inside an LLM; residual risk is architectural. Prompt injection is an **inherently confusable deputy** (CWE-441), not a parser bug a deny-list "fixes."

**Guardrails** are a four-plane runtime, not a system prompt:

| Plane | What lives here | LLM-free for allow/deny? |
| --- | --- | --- |
| **Control** | Identity (user + agent principal), OAuth minting, PAP, PDP (Cedar/OPA/AVP), tool/MCP allowlists, spend ledger, HITL queue, policy bundle hash, audit sink | **Yes** for side effects |
| **Data** | User tokens, retrieved docs, tool/MCP results, screenshots, memory writes, model completions | No -- untrusted token stream |
| **Sandbox** | LLM-generated Python, browser renderer, WASM module, guest kernel | Isolation does not equal authorization |
| **Egress** | Outbound HTTP/DNS/SMTP, DLP, canaries, dest allowlist | **Yes** for dest/PII |

**The model is never the PDP.** Probabilistic detectors (classifiers, instruction hierarchy, spotlighting) **cut likelihood**. Deterministic policy, sandbox, egress allowlists, and bound HITL **bound impact**. DLP/output filters are PEPs for *information* (PII, secrets, CBRN), not for *authority*. A Firecracker microVM that still holds an admin GitHub token is a well-isolated confused deputy.

The core principle: `the model proposes, deterministic code disposes`. The model can suggest an action, but a policy enforcement layer decides whether that action is allowed.

## Why It Matters

A 2024 chatbot producing a bad response is embarrassing. A 2026 agent calling APIs, writing to databases, or triggering payments producing a bad action creates legal liability -- data deleted, money transferred, privileged information forwarded.

OWASP LLM Top 10 **2026** (4 Aug 2026): Prompt Injection held **LLM01**. Excessive Agency climbed to **LLM03** (was LLM06:2025). Unbounded Consumption rose to **LLM06**. Rank-by-raw-incident-count would drop injection out of the top ten -- OWASP keeps it #1 as a **defense-effect**.

The best interview framing is defense in depth:

- Probabilistic detection layers
- Deterministic policy enforcement
- Sandboxing and egress control
- Human approval for consequential actions

That framing immediately separates guardrails from "just prompt the model better."

**Lethal trifecta** (Simon Willison, 16 Jun 2025): an agent that simultaneously has **(1) private data**, **(2) untrusted content**, **(3) outbound communication** can be tricked into exfil. Remove any one leg. Meta's **Rule of Two** (2025) is the floor: simultaneous [A untrusted input, B sensitive data, C state-change/external comms] needs per-action human approval.

**Fine-tuning and RAG do not close LLM01.** Instructions and data are the same token stream. Fine-tuning changes statistical tendency; RAG changes *which* untrusted bytes enter the window. InjecAgent: fine-tuned GPT-4 still **7.1%** ASR on that bench. EchoLeak (CVE-2025-32711, CVSS **9.3**) is zero-click XPIA through retrieved email. Neither weights nor "grounding" create a security boundary.

The OWASP LLM Top 10 2025, NIST AI 600-1, and EU AI Act form the compliance baseline every enterprise deployment must satisfy.

---

## Architecture / System Design

### Six-Layer Guardrail Stack (Product View)

```
+---------------------------------------------------------------------------+
|                      API GATEWAY / LOAD BALANCER                           |
|  +---------------------------------------------------------------------+  |
|  |  Rate Limiting  |  Auth/AuthZ  |  Token Budget Enforcement           |  |
|  +---------------------------------------------------------------------+  |
+---------------------------------+------------------------------------------+
                                  |
+---------------------------------v------------------------------------------+
|  LAYER 1: INPUT VALIDATION                              latency: <100ms    |
|  +------------------+  +-----------------------+  +----------------------+ |
|  | Regex Scanner    |  | BERT Classifier       |  | Input Normalizer     | |
|  | (known inject    |  | (paraphrased attacks, |  | (Base64, Unicode,    | |
|  |  patterns, <1ms) |  |  10-30ms)             |  |  homoglyph decode)   | |
|  +--------+---------+  +-----------+-----------+  +----------+-----------+ |
|           +------------ parallel ---+                        |             |
+-------------------+---------------------------------------+--+-------------+
                    |                                        |
+-------------------v----------------------------------------v---------------+
|  LAYER 2: PROMPT HARDENING (design-time, ~0ms)                              |
|  Role anchoring, delimiter injection resistance, instruction                |
|  hierarchy (system > user > retrieved)                                      |
+-----------------------------------------------------------------------------+
|  LAYER 3: RAG RAIL                                         latency: <80ms   |
|  Source scoring, chunk filtering, poisoned content detection                 |
|  (skipped most often -- EchoLeak-class attacks exploit this gap)            |
+------------------------------------+----------------------------------------+
                                     |
            +------------------------v-----------------------+
            |           LLM INFERENCE                        |
            +------------------------+-----------------------+
                                     |
+------------------------------------v----------------------------------------+
|  LAYER 4: OUTPUT FILTERING                                latency: <150ms   |
|  +----------------+ +--------------+ +------------+ +--------------------+  |
|  | PII Redactor   | | Content      | | Schema     | | Hallucination      |  |
|  | (regex+NER)    | | Moderator    | | Validator  | | Detector           |  |
|  +----------------+ +--------------+ +------------+ +--------------------+  |
+-----------------------------------------------------------------------------+
|  LAYER 5: TOOL-CALL GATING                               latency: <100ms   |
|  Allowlisted tools, scoped credentials, PII-in-args scan,                   |
|  approval gates, sandbox execution, audit logging                           |
|  (skipped second most -- agents leak PII through function args)             |
+-----------------------------------------------------------------------------+
|  LAYER 6: MANAGED MODERATION API                          latency: <50ms    |
|  Probabilistic harm scoring (Llama Guard / cloud moderation)                |
+------------------------------------+----------------------------------------+
                                     v
                             Response to User
```

### Four-Plane Control Architecture (Security View)

A production agent security stack is **two logical planes plus two containment planes**. The model lives only in the data plane.

```
                         TELEMETRY / OBSERVABILITY SINKS
         +----------------------------------------------------------------------+
         |  PDP allow|deny|HITL  arg digest (not secrets)  bundle hash           |
         |  classifier scores  sandbox_id  egress dest  human decision           |
         |  failed tool/API calls (NCSC)  spend reserve  OTel/SIEM WORM         |
         |  MCP 2026-07-28: OTel/stderr -- NOT notifications/message dumps       |
         +--------------^---------------------^------------------^---------------+
                        | spans               | meters            | audit events
+-----------------------------------------------+-----------------------------+
| CONTROL PLANE  (identity, PAP/PDP, spend, HITL, pins -- not token math)      |
|                                                                              |
|  +----------+ +------------+ +--------------+ +-----------+ +----------+     |
|  | IdP/PEP  | | PAP signed | | PDP Cedar /  | | Spend     | | HITL     |    |
|  | OIDC JWT | | bundles    | | OPA / AVP    | | ledger    | | queue    |    |
|  | RFC 8707 | | pin hash   | | allow|deny|  | | reserve $ | | signed   |    |
|  | audience | | toolSurface| | HITL         | | fail-close| | intent   |    |
|  +----+-----+ +-----+------+ +------+-------+ +-----+-----+ +----+-----+    |
+-------+-----------+--------------+----------------+------------+-------------+
        |           |              |                |             |
        v           v              v                v             v
+----------------------------------------------------------------------+
| DATA PLANE  (untrusted token stream -- model proposes, never disposes)|
|                                                                       |
|  input rails -> orchestrator -> FM <-- tool/MCP results (output rails |
|     + DLP before re-injection) -> output rails + DLP to user          |
|                                                                       |
|  +----------- TOOL PROXIES (MCP gateway -- least privilege) --------+ |
|  | tools/call | resources/read | hash-pin verify | token EXCHANGE   | |
|  | Mcp-Method / Mcp-Name per-tool authz/rate w/o body               | |
|  | Identity from verified token -- NEVER from model JSON / tool desc| |
|  | NO client-token passthrough to upstream (MCP MUST NOT)           | |
|  +------------------------------------------------------------------+ |
+---------+---------------------------+---------------------------------+
          |                           |
          v                           v
+---------------------------+  +--------------------------------------------+
| SANDBOX PLANE             |  | EGRESS PLANE                               |
| (untrusted CODE)          |  | (untrusted NETWORK)                        |
|                           |  |                                            |
|  gVisor / Firecracker /   |  |  default-deny NS + L7 proxy + DLP PEP      |
|  WASM / seatbelt          |  |  dest allowlist  DNS to internal resolver   |
|  warm pool; NEVER host    |  |  canaries; no default route                 |
|  exec on pool-empty       |  |  Firecracker net/block limiters in VMM,     |
|  creds OUTSIDE guest      |  |    not a substitute for L7 deny             |
+-------------+-------------+  +--------------------+-----------------------+
              |                                      |
              v                                      v
+----------------------------------------------------------------------+
| PERSISTENCE LAYER                                                     |
|                                                                       |
|  +-----------+ +-----------+ +-----------+ +-----------+ +----------+ |
|  | Policy    | | Tool pin  | | HITL      | | Memory/RAG| | Audit    | |
|  | bundles   | | store     | | signed    | | writes =  | | WORM     | |
|  | (signed,  | | server URI| | intent +  | | effectful | | decision | |
|  | versioned)| | + digest  | | lease     | | PEP       | | logs     | |
|  +-----------+ +-----------+ +-----------+ +-----------+ +----------+ |
+----------------------------------------------------------------------+
```

### Self-Correction Loop (on guardrail failure)

```
+----------+     +-----------+     +------------+
| LLM      |---->| Guardrail |-+-->| Return     |  (pass)
| Generate |     | Check     | |  | Response   |
+----------+     +-----------+ |  +------------+
      ^                   (fail)|
      |          +--------------v--------------+
      +----------| Correction prompt + retry   |--(max 3)--> Block + Log
                 +-----------------------------+
```

### Request-Flow Narrative

1. **Ingress / detect.** TLS + IdP. Strip tag-block U+E0000-E007F, variation-selector U+FE00-FE0F, zero-width U+200B / U+200C / U+200D / U+2060 at ingest *and* render (OWASP LLM01 #5). Input rails: PromptGuard / Llama Guard / Bedrock `ApplyGuardrail` / Azure Prompt Shields / NeMo. Classifier score is a **signal** into the PDP, not an allow. Spend **reserve** against the ledger (fail closed) -- LLM06. Three checks run in parallel: regex (<1ms), BERT classifier (10-30ms), input normalizer (decodes Base64/Unicode/homoglyphs). Any flag blocks with a generic refusal (no info leakage about which detector fired).

2. **Control / PDP.** Orchestrator asks Cedar/OPA/AVP: `(principal=(user, agent_id, tenant, session), action, resource, context)` including originating-user HMAC, MFA, trust score from the **entity store** (not self-asserted), policy bundle hash. Result: **deny** (stop, audit) | **allow** | **require-approval**. Fail closed on AVP errors, schema mismatch, missing entities, signature failure, timeout, unknown action.

3. **Tool gateway / MCP proxy.** Re-verify `toolSurfaceHash` over canonical JSON of name + description + inputSchema + outputSchema. Mismatch leads to session pause (rug pull / CVE-2025-54136 class). Audience-bound token for *this* server (RFC 8707). **MUST NOT** passthrough the client token; RFC **8693** exchange to upstream. `Mcp-Method` / `Mcp-Name` for per-tool rate/authz without parsing JSON-RPC (2026-07-28).

4. **Sandbox (if code).** Lease from warm pool (GKE: **90% <= 200 ms**, **300**/s/cluster). Empty pool leads to queue or **503** -- never unsandboxed host exec. Credentials **outside** the guest (Anthropic git-proxy pattern). Isolation does not equal authorization.

5. **Egress.** Default-deny namespace + L7 proxy. Dest allowlist is the only reliable break of the trifecta's communication leg. PII DLP on tool args to external MCP is **fail-closed**. Canaries on outbound.

6. **HITL if PDP said require-approval.** Return `input_required` / MCP elicitation; persist **signed intent** `hash(principal, action, canonical_args, dest, policy_bundle, expires_at)`; display **raw args**. Do not skip PDP because a human clicked. Re-hash at execute (TOCTOU). Queue timeout leads to **fail closed** on mutating tools (optionally serve read-only).

7. **Execute + re-inject.** Tool/MCP result is untrusted (CyberArk: **every** output channel -- return values, errors, resource metadata/bodies, logs/notifications). Output classifier + DLP **before** bytes re-enter the model. Q-LLM / CaMeL: never give tools to the model that *saw* the raw bytes.

8. **Audit.** Append-only: PDP decision, tool name, **arg digest**, token `jti`, sandbox id, classifier scores, human decision, policy bundle hash. NCSC: log **failed** tool/API calls (attacker rehearsal). Sampled traces are not this tape.

9. If any output layer fails, the self-correction loop retries up to 3 times with targeted correction prompts.

### Product Boxes (where they sit, not an SLO)

| Product | Role in the diagram | Not a PDP |
| --- | --- | --- |
| Llama Guard 3-8B / 4-12B | Input *and* output generative classifier (S1-S13 + **S14** code-interpreter abuse). **S7 Privacy is a safety category, not a DLP engine** | Sensor |
| PromptGuard 2 (22M / 86M) | BERT-scale injection scan (LlamaFirewall) | Sensor |
| LlamaFirewall | Last layer: PromptGuard 2 + AlignmentCheck + CodeShield (8 languages) | Sensor; Agent-as-a-Proxy still attacks it |
| Bedrock Guardrails | Content / denied topics / PII / grounding / Automated Reasoning. `guardrailId` or `ApplyGuardrail` / `InvokeGuardrailChecks`. Input policies **parallel** (AWS latency claim, no percentile) | Information + topic PEP; not tool authz |
| Azure Prompt Shields | User-prompt (jailbreak) + document (indirect); Foundry `action: annotate | block`, `spotlighting_enabled` **off by default** | App must enforce |
| NeMo Guardrails | Colang flows; library or Envoy `ext_proc` sidecar. `failure_mode_allow: false` = mesh fail-closed. Mutating input rails in parallel **race** -- sequential then | Rails are sensors + I/O validation |
| Constitutional Classifiers | CBRN/RSP; v1 input+output; CC++ probe then exchange ensemble | Safety classifier, not Cedar |
| AgentCore Policy | Cedar (or Dogwood) at the **gateway** on every tool call; Guardrails scores as information providers | This *is* a PDP when it evaluates Cedar |

---

## Core Concepts & Algorithms

### Invariants

**I1. The model is never the PDP.** Classifiers reduce likelihood. Policy, sandbox, egress, bound HITL bound impact.

**I2. Instructions and data share one token stream.** Fine-tuning and RAG do not create a parameterized-query boundary. Context-window pooling + memory persistence + agentic re-injection are the 2026 amplifiers (LLM01).

**I3. Isolation does not equal authorization.** Sandbox without scoped credentials is a confused deputy with a guest kernel.

**I4. Principal is `(user, agent_id, tenant, session)` -- never "the LLM."** `{read_mail}` does not equal `{read_mail, send_mail}` (LLM03 mailbox story).

### Prompt Injection: The #1 Threat

Prompt injection exploits the fundamental LLM design: instructions and data are processed in the same channel without clear separation. No complete fix exists.

**Injection ingresses (where each is blocked):**

| Class | Ingress | Probabilistic block | Deterministic block |
| --- | --- | --- | --- |
| **Direct** | User chat / `messages[]` | Prompt Shields / PromptGuard / Llama Guard input | Role + schema; no extra tools for untrusted users |
| **Indirect (XPIA)** | Web, email, PDF, ticket, OCR | Spotlighting; document Prompt Shields | Dual-LLM / CaMeL; Q-LLM has **no** tools |
| **Tool-result / ATPA** | `tools/call` body, errors, MCP `content` | Output classifier before re-injection | Treat result as untrusted; never tool-on-raw |
| **Tool-description / TPA** | `tools/list` description + JSON Schema | Catalog scanners | Hash-pin **entire** tool JSON; re-consent on drift |
| **Rug pull** | Post-approval mutation | -- | Pin hash; pause on mismatch. CVE-2025-54136 CVSS **8.8** (Cursor MCPoison; patched 1.3) |
| **MCP resource** | `resources/read`, templates, `resource_link` | Same as XPIA | Sanitize URIs; `resource_link` need not appear in `resources/list` |
| **Memory poisoning** | Cross-session store | Write classifier | Memory write is effectful PEP; HITL for instruction-bearing memories |
| **Multimodal** | Image / audio + user text | Llama Guard 4 | OCR/transcribe then text filters |
| **Client RCE via metadata** | OAuth `authorization_endpoint` | -- | Treat server metadata as hostile. CVE-2025-6514 CVSS **9.6** (`mcp-remote` 0.0.5-0.1.15; fixed 0.1.16) |
| **Encoding (Base64, ROT13, homoglyphs)** | Encoded payloads | -- | Input normalizer (decode before classification) |
| **Multilingual evasion** | Attack in untrained language | Near-total if guardrail is English-only | Multi-language classifiers |

**Critical CVEs (2025-2026)**:
- **EchoLeak** (CVE-2025-32711, CVSS 9.3): Crafted email in inbox; Copilot's RAG retrieved it during unrelated query; hidden instructions exfiltrated chat logs. Zero-click, no jailbreak.
- **GitHub Copilot** (CVE-2025-53773, CVSS 9.6): Source file instructions achieved remote code execution by disabling user confirmation.
- **MCPoison** (CVE-2025-54136, CVSS 8.8): Tool-description poisoning via MCP; patched in Cursor 1.3.
- **mcp-remote RCE** (CVE-2025-6514, CVSS 9.6): Connecting to hostile `authorization_endpoint` metadata caused host RCE before any tool call.
- Researchers achieved **100% evasion** against Azure Prompt Shield using Unicode injection and adversarial ML.

Jailbreak (bypass *vendor* safety) vs prompt injection (hijack *application* behavior) overlap in technique; they differ in who is harmed. OWASP: jailbreaking is a subset of direct injection whose goal is safety-protocol violation.

**Why FT/RAG fail (numbers are bench-specific):** InjecAgent (1,054 cases, 17 user tools, 62 attacker tools): ReAct GPT-4 ASR **24%** base / **47%** with hacking-prompt enhancement; fine-tuned GPT-4 **7.1%** (GPT-3.5 FT **6.6-8.4%**); prompted Llama2-70B **>80%** both settings. Instruction hierarchy (OpenAI IH-Challenge, 2026): GPT-5-Mini-R average robustness **84.1% -> 94.1%**; unsafe **6.6% -> 0.7%**; still inside the confusable deputy. Fun-tuning (LLM01 #7): **65-82%** ASR on Gemini in Labunets et al. 2025. Nasr adaptive attacks **>90%** vs many static wrappers.

### Three-Tier Input Defense

```
Tier 1: Regex          Cost: ~$0     Latency: <1ms     Catches: ~30% known
Tier 2: BERT           Cost: ~$100/mo Latency: 10-30ms  Catches: ~70% known
Tier 3: LLM Evaluator  Cost: per-call Latency: 200-800ms Catches: ~90% known
```

### Architectural Defenses (Increasing Strength)

**A. Instruction hierarchy** -- model-level, probabilistic. Necessary, insufficient.

**B. Spotlighting** (Hines et al., 2024; Azure Prompt Shields / Foundry). Delimiting (weakest) / datamarking (recommended default) / encoding (strongest on GPT-4 class; do **not** use on weak models). Headline: GPT-family ASR **>50% -> <2%** in *their* XPIA corpus -- not a universal SLO. Foundry: off by default; no direct API surcharge; Base64 grows tokens (**~+33%** chars).

**C. Dual LLM** (Willison, 2023). P-LLM sees trusted user intent, has tools. Q-LLM sees untrusted documents, **has no tools**. Controller passes **symbolic handles**, never raw Q-LLM text, to the P-LLM. Pasting the summary into P-LLM destroys the pattern.

**D. Beurer-Kellner six patterns** (arXiv:2506.08837): Action-Selector; Plan-Then-Execute (CFI on *which* tools -- calendar injection can still rewrite an email *body*); LLM Map-Reduce; Dual LLM; Code-Then-Execute (CaMeL); Context-Minimization. Appendix: sandbox + HITL are **universal best practices**.

**E. CaMeL** (Debenedetti et al., arXiv:2503.18813). P-LLM emits restricted Python from the **trusted** query only. Q-LLM extracts fields, never gets tools. Interpreter **capability-tags** every value; tool calls only if data-flow satisfies policy. AgentDojo: **77%** tasks with *provable* security vs **84%** undefended (**-7 pp** utility). Research implementation, not a complete product. **Do not compare 84% AgentDojo utility to LlamaFirewall's 47.7%** -- different model sets, attack slices, and scoring.

**F. PlanGuard** (Gong et al., arXiv:2604.10134, 2026). Isolated planner sees **only** user instruction and tool definitions. Stage I: deterministic allowlist. Stage II: LLM intent verifier. InjecAgent: ASR **72.8% -> 0%**; combined FPR **1.49%**. Stage-I-only FPR **27.00%** (DH) / **38.01%** (DS); Stage II recovers to **0.97% / 3.28%**. ASR **0%** is **structural on that bench**, not an SLO. PlanGuard = CFI on *which tools*; CaMeL = provenance PEP on *which values*.

**G. Allowlists (required).** (1) tool pack per role; (2) argument JSON Schema + server-side validation, path/URL allowlists inside args; (3) egress allowlist -- default-deny outbound.

### PEP/PDP and Cedar L1-L3

**PEP vs PDP.** Policy Enforcement Point sits on every *effectful* hop: `tools/call`, `resources/read`, sandbox exec, egress HTTP, memory write, spend reservation. Policy Decision Point answers allow / deny / require-approval given `(principal, action, resource, context)`. The model **proposes**; code **disposes** (OWASP LLM01 #4; AWS Cedar sample; NeMo execution rails). If the disposer is another LLM ("LLM-as-policy"), you have a second confusable deputy.

AWS three-layer Cedar (2026): **L1** agent->tool (registered agent, trust score from entity store, lifecycle=prod). **L2** agent->agent (max hop depth: example cap **5**, destructive example **2**; capability subset of target's registered set). **L3** originating user (role + `mfa_verified` on `context.originating_user`). Agent remains the Cedar principal; human is context. Originating-user context is HMAC-SHA256-signed by the MCP adapter. AuthN (OIDC) is **outside** Cedar. Cedar policies are order-independent (**forbid wins**).

### MCP OAuth 2.1 + RFC 8707 (Normative)

**2025-11-25:** `initialize` handshake; `Mcp-Session-Id`; capabilities once. Remote HTTP: **OAuth 2.1**; PKCE (`S256` when capable); clients **MUST** send RFC **8707** `resource` on authorize *and* token requests naming the **canonical MCP server URI**; servers **MUST** accept only tokens whose audience is themselves; **MUST NOT** passthrough the client token.

**2026-07-28 (stateless core):** `initialize` and `Mcp-Session-Id` **removed**. Each request carries protocol version, client identity, capabilities in `_meta`. Optional `server/discover`. `ttlMs` / `cacheScope` on `tools/list` -- a long TTL without re-hash is a rug-pull window. Streamable HTTP: `Mcp-Method` / `Mcp-Name`. Identity in the token; pins in a store keyed by server URI + digest.

**Confused deputy (proxy):** static third-party `client_id` + DCR + consent cookie **MUST** collect **per-dynamic-client** user consent before forwarding. `state` cookie **MUST NOT** be set until after MCP-server consent. stdio MCP: HTTP OAuth profile does not apply; host-env credentials are often worse.

Token-passthrough risks named by the spec: control circumvention, broken audit, stolen-token exfil proxy, trust-boundary collapse.

### Sandbox Isolation Models

| Primitive | Isolation | Published figure | Fit |
| --- | --- | --- | --- |
| **runc** | Shared host kernel | Fast; **not** a security boundary for hostile code | Trusted internal jobs |
| **gVisor** | User-space kernel; syscalls *interpreted* | Shrinks System API surface; no side-channel claim; host cgroups for DoS. `directfs` / host-net **widen** the host API | GKE Agent Sandbox default |
| **Firecracker** | KVM + guest kernel; jailer required | VMM RSS **<= 5 MiB**; **<= 125 ms** InstanceStart to `/sbin/init` (**spec max, not p99**); **150** microVMs/s/host; compute-only guest **> 95%** bare metal | Multi-tenant **code exec** |
| **WASM / WASI 0.2** | Linear memory; default-deny imports | Microsecond-class instantiate | Interpreters, OPA WASM -- **not** CPython+native wheels |
| **Seatbelt / bwrap** | OS FS + network | Anthropic: **84%** fewer permission prompts (internal usage, not latency). Codex: network **off** by default | Local coding agents |
| **Chromium Site Isolation** | Renderer per site | Default since Chrome 67 | Agent *browsing*; page bytes are still LLM fuel |

NumaVM (2026-03-10): Firecracker's 125 ms is **not** SSH-ready. Full cold boot to SSH: **1,133 ms** (orchestration 263 ms + kernel/init 560 ms); snapshot restore to SSH **176 ms**; `/snapshot/load` **25 ms**. Do not quote 125 ms as user-facing p50. Snapshots are TCB -- poisoned snapshot = persistent malware.

### Hallucination Detection Methods

| Method | How It Works | Strength | Weakness |
|--------|-------------|----------|----------|
| Retrieval-based (RAG Triad) | Cross-ref vs sources | High precision | Cannot detect source errors |
| ECE (Expected Calibration Error) | Confidence vs correctness gap | Catches high-confidence hallucinations | Needs calibration dataset |
| Self-consistency | Multiple responses, check agreement | Simple | Fails on consistent errors |
| Decomposition (HaluCheck) | Atomic fact verification | Granular, explainable | Expensive |

Key finding: token-level entropy fails on high-confidence hallucinations. Larger models can be less truthful on certain categories (TruthfulQA).

### PII Detection: Four Leakage Vectors

Each requires a **separate** control:

| Vector | Where | Control |
|--------|-------|---------|
| Training data memorization | Model weights | Model-level mitigation |
| User-submitted PII | Inputs | Pre-LLM guardrail (regex+NER, <20ms) |
| PII in retrieved context | RAG pipeline | Retrieval rail |
| Hallucinated PII | Outputs | Post-LLM guardrail |

Redaction replaces PII with `[NAME]` (irreversible). Masking substitutes synthetic placeholders (reversible with stored mapping). Under GDPR Article 4(5), pseudonymized data remains personal data when linkable.

### Multi-Turn Attack Detection

NeMo Guardrails uses Colang 2.0 state machines tracking conversation flow. Detects adversaries gradually shifting conversations toward policy violations across turns -- invisible to single-turn classifiers.

### OWASP LLM Top 10 2025 Defense Mapping

| Rank | Vulnerability | Primary Defense Layer |
|------|--------------|----------------------|
| LLM01 | Prompt Injection | Input validation + Tool-call gating |
| LLM02 | Sensitive Info Disclosure | Output PII filtering |
| LLM03 | Supply Chain | Design-time provenance |
| LLM04 | Data/Model Poisoning | RAG Rail |
| LLM05 | Improper Output Handling | Output Schema validation |
| LLM06 | Excessive Agency | Tool-call gating (Least Privilege) |
| LLM07 | System Prompt Leakage | Prompt Hardening |
| LLM08 | Vector/Embedding Weaknesses | RAG Rail |
| LLM09 | Misinformation | Output filtering + Moderation |
| LLM10 | Unbounded Consumption | Gateway rate/spend limits |

---

## Token Economics & Cost Analysis

### Cost by Configuration

| Configuration | Latency Added | FPR | Attack Block Rate | Monthly Cost (1M req/day) |
|--------------|--------------|-----|-------------------|--------------------------|
| Regex only | <1ms | ~1% | ~30% | ~$0 |
| Regex + BERT | 10-30ms | ~2% | ~70% | ~$50-100 |
| Regex + BERT + Llama Guard | 50-100ms | ~4% | ~90% | ~$200-500 |
| Full 6-layer stack | 90-250ms | ~5% | ~95%+ | $500-2,000+ |

No guardrail achieves 100% against novel adversarial techniques.

### Bedrock Guardrails Pricing

One **text unit = <= 1,000 characters**. Filters are **additive**. Word filters and regex PII are **$0**. Same price standard vs classic.

| Filter | Price |
| --- | --- |
| Content filters (text) | **$0.15** / 1,000 text units |
| Content filters (image) | **$0.00075** / image |
| Denied topics | **$0.15** / 1,000 text units |
| Sensitive information (ML PII) | **$0.10** / 1,000 text units |
| Sensitive information (regex) / word filters | **Free** |
| Contextual grounding (source+query+response chars) | **$0.10** / 1,000 text units |
| Automated Reasoning | **$0.17** / 1,000 text units **per policy** |
| `InvokeGuardrailChecks` content-only | **$0.07** / 1,000 text units |
| `InvokeGuardrailChecks` prompt-attack | **$0.08** / 1,000 text units |

Blocked input pays Guardrails, **not** FM inference. Blocked output pays Guardrails **and** tokens already generated.

**Worked examples:** Support chatbot 1,000 queries/h, 200-char in + 1,500-char out = 3 units/query. Content + denied topics = **$0.90/h** = **$0.90 per 1k queries**. Content + PII only on same mix = **$0.75 per 1k** [inferred]. 10,000 transcript summaries x 3,500 chars = 4 units x $0.10/1k = **$4** PII-only. Automated Reasoning diagnostic: 5,000 cases x (2+5) units x $0.17/1k = **$6.80/month**.

**[inferred] ~4 chars/token** => 1 text unit ~ 250 tokens => content filter ~ **$0.60 per 1k tokens of scanned text**, PII ~ **$0.40 / 1k tokens**.

### Constitutional Classifiers Cost

| Generation | Jailbreak ASR (automated, *their* bench) | Over-refusal / flag | Compute vs unguarded |
| --- | --- | --- | --- |
| Unguarded Claude 3.5 | **86%** | -- | 1.0x |
| CC v1 (input+output) | **4.4%** | **+0.38 pp** on 5k prod convos (n.s.) | **+23.7%** |
| CC++ (probe -> exchange) | "No universal jailbreak" in *their* RT (8 CBRN queries) | **0.05%** flag on Sonnet 4.5 shadow | **~1%** extra if applied to Opus 4.0 traffic; **~40x** cheaper than a single exchange classifier |

CC v1 red team: 183 active, **>3,000 h**, $15k bounty. CC++: **>1,700 h**, 198k attempts; production-grade high-risk vuln discovery **0.005 / 1,000** queries.

### Azure Prompt Shields Pricing

F0 **5 RPS**; S0 **1000 RP10S**. Free: **5,000 text records/month** (1 record = <= 1,000 Unicode code points). 2023 launch blog: **$0.75 / 1k text records**. Microsoft Q&A (2026) cites **$0.38 / 1k records** as current S-tier.

### Latency SLA Targets

**Published facts (ms):**

| Event | Published figure | Percentile? |
| --- | --- | --- |
| Firecracker start to init | <= **125 ms** | Spec **max**, not p99 |
| Firecracker VMM RSS | <= **5 MiB** | Spec |
| Firecracker create rate | **150**/s/host | Spec / site |
| GKE Agent Sandbox allocate | **90% <= 200 ms**; **300**/s/cluster | **p90** |
| Firecracker full cold to SSH (NumaVM) | **1,133 ms** | Lab **mean**, not vendor SLO |
| Snapshot restore to SSH (NumaVM) | **176 ms** | Lab |
| ApplyGuardrail sample (5 serial vs 1 batch) | **43,690 ms** vs **230 ms** | Sample, not SLO |
| PromptGuard 2 86M H100 FP8 short | **20-50 ms** | Third-party blog -- **not Meta** |
| Kastra Cedar Rust | p50 **0.62 ms** / p99 **2.30 ms** | Vendor bench -- **not independent** |
| Kastra OPA sidecar HTTP | p50 **3.10 ms** / p99 **12.20 ms** | Same |

**[inferred] policy targets:**

| Path | **p50** | **p95** | **p99** | Grounding / mitigation |
| --- | --- | --- | --- | --- |
| **PDP Cedar in-process** | **1 ms** | **2 ms** | **3 ms** | Put in-process on the tool gateway, not cross-AZ |
| **PDP OPA sidecar HTTP** | **3 ms** | **8 ms** | **12 ms** | Industry 1-5 ms RTT |
| **PromptGuard 2 86M inline** | **35 ms** | **50 ms** | **100 ms** | Meta unpublished |
| **Llama Guard generate ON user path** | **800 ms** | **2,500 ms** | **8,000 ms** | Full LLM generate -- keep off mutating-tool p50 |
| **Bedrock ApplyGuardrail batched** | **230 ms** | **500 ms** | **1,500 ms** | **Never** 5 serial calls |
| **GKE Agent Sandbox allocate** | **80 ms** | **200 ms** | **500 ms** | p99 -> 503, never unsandboxed |
| **Firecracker cold to SSH-ready** | **1,133 ms** | **1,800 ms** | **3,000 ms** | Prefer snapshots |
| **Snapshot restore to SSH** | **176 ms** | **300 ms** | **600 ms** | Snapshot = TCB |
| **WASM instantiate** | **1 ms** | **2 ms** | **5 ms** | Typically sub-ms |
| **HITL mutating-tool clock** | **30,000 ms** | **180,000 ms** | **600,000 ms** | Durable queue; p99 = expire then deny |

**By layer (aggregate overhead):**

| Layer | p50 | p95 | p99 |
|-------|-----|-----|-----|
| Input validation (parallel) | <30ms | <50ms | <80ms |
| RAG rail | <80ms | <120ms | <200ms |
| Output filtering (parallel) | <150ms | <200ms | <250ms |
| Tool-call gating | <100ms | <150ms | <200ms |
| **Aggregate overhead** | **~90ms** | **~150ms** | **~250ms** |

### False Positive Impact

| Model/System | FPR | Blocked per 1M daily requests |
|-------------|-----|-------------------------------|
| Llama Guard 3 (8B) | ~4% | 40,000 |
| GPT-4 moderation | ~15.2% | 152,000 |
| Custom BERT | ~2% | 20,000 |

A single F1 on a mixed test set hides false positive / false negative asymmetry. Testing requires a reviewed safe set concentrated near the policy boundary.

### Availability and Capacity

| Concern | Target | Rationale |
|---------|--------|-----------|
| Guardrail throughput | 10k req/s per instance | Must not bottleneck |
| Guardrail availability | 99.99% | Downtime = unprotected traffic |
| RPO (audit logs) | 0 | Compliance requires complete trail |
| RTO (guardrail service) | <30 seconds | Fail-closed during recovery |
| Classifier refresh | Weekly eval, monthly retrain | Attack patterns evolve |

### Complexity of Extra Classifier Hops

- **Sequential mutating rails:** total time = sum of all rail times. NVIDIA: mutating input rails in parallel **race** -- sequential then.
- **Parallel non-mutating rails:** total time = max of all rail times (+ merge).
- **Dual LLM / CaMeL:** every untrusted extract is a second model call. [inferred] If 30% of turns touch untrusted docs and Q-LLM is 0.25x P-LLM price, additive cost ~ **7.5%** of P-LLM spend. Utility tax **is** measured: **84% -> 77%**.
- **CC++ cascade:** first-stage probe escalates **~5.5%** of traffic -> **~1%** extra compute vs CC v1 **+23.7%**.
- **Bedrock streaming without batching** multiplies `ApplyGuardrail` RPS. Sample: 5 serial calls **43.69 s** vs one batched 5-block **0.23 s** (~**190x**).
- **PDP vs FM:** a 2-10 ms PDP is noise vs decode; put PDP **in-process on the tool gateway**, not a cross-AZ HTTP call.
- **HITL** is seconds-minutes, not ms. p99 of a tool-using agent is usually **HITL + cold sandbox + classifier cascade**, not the PDP.

---

## Trade-offs & Failure Modes

### Fail-Open vs Fail-Closed Matrix

Write the matrix in the PAP. Do not let on-call "temporarily skip Guardrails" without a ticket.

| Subsystem | Default when down | Why |
| --- | --- | --- |
| Authorization (Cedar/OPA/AVP) | **Fail closed** | Allow-on-timeout is a 0-day for every tool |
| Spend / rate caps | **Fail closed** | LLM06 |
| Sandbox create | **Fail closed** (no host exec) | SEV-0 |
| CBRN / CSAM / weapons / exfil tools | **Fail closed** | CC++ treats FPR as *escalation* inside the stack |
| Topic/brand "niceness" classifiers | **Fail open + alert** | Blind fail-closed on a 23.7% overhead classifier takes the product down |
| PII DLP (user-facing chat) | Often **fail closed to mask** | UX vs compliance |
| PII DLP on tool args to external MCP | **Fail closed** | Exfil |
| Prompt-injection detector | Fail open + score in audit for low-agency chat; **fail closed** if next hop is `send_email` / `shell` | Detector FPR otherwise DoS the agent |
| Egress proxy | **Fail closed** (default-deny) | Trifecta communication leg |
| HITL service | **Fail closed** on mutating tools | Do not auto-approve on queue timeout |

### Common Failure Modes

| Failure | Cause | Detection | Mitigation |
| --- | --- | --- | --- |
| **System prompt as "security"** | Model is not a PDP; Nasr adaptive **>90%** vs static defenses | Exfil despite "never send" | Deterministic PEPs + trifecta break |
| **PDP fail-open** | Tools run during AVP timeout | Audit gap; on-call runbook | Fail-closed matrix in PAP; stale-deny cache |
| **FT / RAG as fix** | InjecAgent FT GPT-4 still **7.1%** ASR; EchoLeak RAG pipe | Bench ASR != 0; inbox zero-click | Architecture, not weights |
| **Tool-description poisoning** | Descriptions are trusted context (Invariant Labs) | Agent "because the tool said to" | Hash entire schema; private registry |
| **Rug pull** | CVE-2025-54136; `ttlMs` cache without re-hash | Thursday digest != Monday consent | Pin + re-consent + call-time verify |
| **ATPA / all MCP channels** | Defense sanitizes only `content[0].text` | Secrets emailed after a "successful" fetch | Scan every output channel; Dual-LLM |
| **Token passthrough** | Convenience; downstream logs wrong principal | Spec-forbidden | RFC 8707 + 8693 exchange |
| **Approval fatigue** | Too many prompts train users to approve without review | User clicks Approve on injected send | Raw-arg binding; sandbox cut prompts 84% |
| **Memory poisoning** | Sleeper write ASR **99.8%** on GPT-5.5 | Weeks later "user preference" | Memory PEP; origin tags; no web-to-semantic memory |
| **Container-only isolation** | runc shares host kernel | Cross-tenant read after kernel 0-day | Firecracker/Kata for hostile multi-tenant code |
| **Sandbox with god-token** | Isolation != authz | Isolated RCE still has prod creds | Scoped tokens; credentials outside guest |
| **Over-blocking -> disable guards** | PlanGuard Stage I FPR 27-38%; CC v1 chemistry FPs | Support tickets; `failure_mode_allow: true` | Cascade; shadow mode; overblock budget (CC++ **0.05%**) |
| **Classifier-as-PDP** | "Llama Guard said safe, so send_email"; S7 != DLP | Sensor treated as allow | Sensors vs enforcement |
| **Latency kill (>400ms, team disables)** | Guardrail at 400ms p50 becomes the latency story | p95 monitoring | Parallel execution, faster classifiers |
| **Guardrail drift** | Model updates, prompt template changes, novel attacks | Regression tests | CI/CD guardrail tests, weekly red-team |
| **Denial of wallet** | Retry x tools x classifier overnight | Overnight $ spike | Ledger reserve; max steps; breaker |
| **EchoLeak-class zero-click** | Classifier + markdown + CSP chain | Email in inbox -> exfil, no click | Prompt partitioning, output URL allowlist |

### HITL Binding and TOCTOU

Bind approval to `hash(principal, action, canonical_args, dest, policy_bundle, expires_at)`, not a model-authored summary. Underspecified canonicalization creates **approval hash collision**. Strip invisible Unicode at ingest *and* HITL UI so displayed action = executed action.

**TOCTOU (CWE-367):** (1) args change between render and execute -- re-hash at execute; (2) FS tools: path check then `open()` races with symlink swap -- `openat2(RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS)`, not string-then-open; (3) MCP rug pull: consent-time schema != call-time schema -- re-verify digest on every `tools/call`.

Anthropic sandboxing cut prompts **84%** internally by moving the boundary from "ask every command" to "ask on sandbox escape." Durable queue (Temporal / elicitation) belongs under HITL, not a chat HTTP timeout.

### NFRs and Explicit Trade-offs

| NFR | Production stance | Competes with |
| --- | --- | --- |
| **Availability of chat vs of tools** | Chat may fail-open niceness classifiers. Tools / spend / egress / sandbox-create **fail closed** | Overblock -> teams set `failure_mode_allow: true` |
| **RPO of policy versions** | Last **signed** bundle hash that PEPs pin. Cedar forbid-wins eases concurrent PAP edits | Velocity of policy edits vs "stale allow" |
| **RTO of policy versions** | Flip pin to previous signed bundle (seconds) vs "AVP down" (fail closed) | Time-to-recover vs fail-closed outage |
| **RPO of HITL** | Signed intent in durable queue. Chat HTTP timeout is **not** RPO | User p99 vs irreversible send |
| **CaMeL utility vs isolation** | **77%** vs **84%** AgentDojo (**-7 pp**) for *provable* security | "Just call the tool" vs capability tags |
| **Sandbox compat vs escape resistance** | runc: highest compat, lowest hostile-code resistance. gVisor: syscall holes. Firecracker: high escape resistance. WASM: us start, no native wheels | Agent "can't run this image" vs tenant isolation |
| **Compliance** | SOC2 CC6/CC7: complete mediation at PEPs. HIPAA: PHI in prompts/tool args/logs is a disclosure. GDPR: purpose limitation, erasure vs immutable audit via **arg digests** | Utility vs isolation |

---

## Production Patterns & Best Practices

### Circuit Breakers (Fail-Closed for Tools)

A guardrail at 400ms p50 becomes the latency story for the whole product. Teams disable it. The guardrail never goes back on. This is how guardrails die.

```
        PDP 5xx/timeout | classifier error-rate | MCP hung | sandbox pool empty
  +----------+  ------------------------------------------------>  +----------+
  |  CLOSED  |                                                      |   OPEN   |
  | evaluate |  success resets consecutive count                    | FAIL FAST|
  +----+-----+                                                      | DENY tool|
       ^                                                            | NEVER    |
       | probe OK                                                   |  skip    |
       |                                                            +----+-----+
       |                                                                 | cooldown
       |                                                           +-----v------+
       +------------ probe allow -----------------------------------| HALF-OPEN |
                    probe fail -> stay OPEN / DENY                  | 1 synthetic|
                                                                    | probe; DENY|
                                                                    | if fail   |
                                                                    +------------+
```

**Thresholds [policy]:**

| Trip condition | Closed to open | Half-open probe | Fallback (not "skip") |
| --- | --- | --- | --- |
| PDP sidecar 5xx/timeout | consecutive >= **5** or error-rate window | Synthetic `(probe_principal, read-only action)` | **Stale-deny-all** for high-risk actions |
| Classifier NIM / ApplyGuardrail | error-rate + p99 latency | Synthetic benign + known-bad probe | PAP matrix: fail-open *niceness* + alert; **fail-closed** if next hop is effectful |
| MCP server hung | concurrency + latency breaker (`Mcp-Name`) | One cheap `tools/list` / discover | Deny that server's mutating tools |
| IdP / token endpoint | auth fail window | One token refresh | Fail closed on tool calls |
| Sandbox pool empty | allocate 503 / timeout | One allocate | Queue or 503 -- **never** unsandboxed exec |

### Zero-Trust MCP Minimum

- OAuth 2.1 + PKCE S256
- RFC 8707 audience = canonical MCP server URI
- **No** token passthrough (RFC 8693 exchange)
- Per-dynamic-client consent
- Hash-pinned tools re-verified on every `tools/call`
- Hostile metadata (do not `open()` unsanitized `authorization_endpoint`)
- Short-lived per-invocation tokens for secrets/prod data

### Tool-Level RBAC

| IAM idea | Agent equivalent |
| --- | --- |
| Principal | `(user, agent_id, tenant, session)` -- never "the LLM" |
| Role | Tool pack: `{read_mail}` != `{read_mail, send_mail}` |
| Scope | OAuth 2.1 scopes on the **tool's** token, audience-bound |
| Delegation | Cedar L2: hop count + capability subset |
| Break-glass | HITL for irreversible actions |

### PII DLP Pipeline -- Detect, Redact, Audit

On user input, model output, **tool args to external MCP**, log/trace path, and HITL UI, **before** egress and **before** SIEM persist.

1. **Detection (control plane, before the bytes leave).** Dual-gate: **regex** (email, US SSN, US phones, PAN -- Bedrock regex PII is **$0**) + **ML NER/classifier** (Bedrock sensitive-info **$0.10**/1k text units; Azure text records). Llama Guard **S7 is not this engine**. If the ML classifier is down: **fail closed to mask** on user-facing chat; **fail closed (block)** on tool args to external MCP.

2. **Redaction.** ANONYMIZE to stable tokens (`[EMAIL_<hash12>]`, `[PAN]`) so the task can continue without leaking. BLOCK when policy says the field must not exist. Strip invisible Unicode at the same boundary.

3. **Audit trail (WORM).** Immutable log of detect/redact **decisions**, not values: `content_sha256` pre- and post-redact, entity **types** + counts, action (`tokenize` / `mask` / `block-from-egress` / `block-from-tool`), detector, `correlation_id`, `tenant`, `policy_bundle_hash`, PDP decision. A tool call without an audit row is a control-plane bug.

### Durable Execution for Guardrail Pipelines

1. **Temporal integration**: Each guardrail check runs as a Temporal Activity with its own retry policy. Input validation = short timeout (5s), 3 retries. Content classification = longer timeout (15s), 2 retries. If guardrail service is down, the workflow pauses (not fails).

2. **Kafka-backed pattern**: Guardrail requests published to `guardrail-requests` topic. Consumer group processes checks. Results published to `guardrail-results` topic. Dead-letter topic for messages that fail after max retries. Consumer commits offset only after guardrail result is persisted.

### Framework Selection Guide

| Use Case | Tool | Rationale |
|----------|------|-----------|
| Content moderation | Llama Guard 3 (8B) | F1 0.939, 4% FPR, self-hostable |
| Fast pre-filter | Qwen3-Guard (0.6B) | Minimal latency, obvious violations |
| Prompt injection | Granite Guardian | Leads on injection categories |
| Multi-turn safety | NeMo Guardrails + Colang 2.0 | State-machine multi-turn tracking |
| Structured output | Guardrails AI + RAIL | Per-field typed validators with retry |
| Agent security | LlamaFirewall | 17.6% to 1.75% attack success at Meta |

### Compliance Timeline

| Date | Regulation | Requirement |
|------|-----------|-------------|
| Aug 2025 | EU AI Act: GPAI | Document capabilities, limitations, safety |
| 2025 | OWASP LLM Top 10 v2025 | 25% weight from ~7,714 real incidents |
| 2025 | NIST AI 600-1 | AI risk management for generative AI |
| Aug 2026 | EU AI Act: High-risk | Full compliance for high-risk AI systems |

---

## Code Examples

### Production Guardrail Harness (stdlib, runnable)

Wired: retries + full jitter, circuit breaker **fail-closed for tools**, fallback **PDP deny -> HITL -> refuse**, PII detect-redact-audit, hash-pin verify, egress allowlist, sandbox pool that 503s instead of host-exec, HITL TOCTOU binding, structured logs with correlation IDs.

```python
#!/usr/bin/env python3
"""Runtime guardrails: PDP, sandbox, egress, HITL, PII detect->redact->audit.

Stdlib only. Swap FakePdp / FakeClassifier for Cedar AVP / Bedrock ApplyGuardrail.
Run: python guardrails_harness.py
"""
from __future__ import annotations

import hashlib, json, logging, random, re, threading, time, uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

PDP_FAIL_CLOSED = {"send_email", "shell", "crm.export"}
NICENESS_RAILS = {"topic_brand"}


class CorrelationFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        for k, d in (("correlation_id", "-"), ("tenant_id", "-"),
                     ("decision", "-"), ("bundle_hash", "-")):
            setattr(record, k, getattr(record, k, d) or d)
        return True


def _log() -> logging.Logger:
    log = logging.getLogger("guardrails")
    if not log.handlers:
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter(
            '{"ts":"%(asctime)s","level":"%(levelname)s",'
            '"cid":"%(correlation_id)s","tenant":"%(tenant_id)s",'
            '"decision":"%(decision)s","bundle":"%(bundle_hash)s",'
            '"msg":"%(message)s"}'
        ))
        h.addFilter(CorrelationFilter())
        log.addHandler(h)
        log.setLevel(logging.INFO)
    return log


LOG = _log()


def retry_with_jitter(fn: Callable, *, attempts: int = 4, base: float = 0.05,
                      cap: float = 1.0):
    """Exponential backoff + full jitter (AWS-style). Raises last error."""
    last = None
    for i in range(attempts):
        try:
            return fn()
        except TransientError as e:
            last = e
            time.sleep(random.uniform(0, min(cap, base * (2 ** i))))
    raise last


class TransientError(Exception):
    pass


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Fail-CLOSED for tools: OPEN denies; it never skips the PEP."""

    def __init__(self, name: str, fail_max: int = 5, cooldown_s: float = 2.0):
        self.name, self.fail_max, self.cooldown_s = name, fail_max, cooldown_s
        self.state = CircuitState.CLOSED
        self.fails = 0
        self.opened_at = 0.0
        self._lock = threading.Lock()

    def allow_probe(self) -> bool:
        with self._lock:
            if self.state is CircuitState.CLOSED:
                return True
            if self.state is CircuitState.OPEN:
                if time.time() - self.opened_at >= self.cooldown_s:
                    self.state = CircuitState.HALF_OPEN
                    return True
                return False
            return True  # half-open: one probe

    def record(self, ok: bool) -> None:
        with self._lock:
            if ok:
                self.fails = 0
                self.state = CircuitState.CLOSED
                return
            self.fails += 1
            if self.state is CircuitState.HALF_OPEN or self.fails >= self.fail_max:
                self.state = CircuitState.OPEN
                self.opened_at = time.time()


class Decision(Enum):
    ALLOW = "allow"
    DENY = "deny"
    HITL = "hitl"


@dataclass
class AuditRow:
    correlation_id: str
    tenant_id: str
    action: str
    decision: str
    arg_digest: str
    bundle_hash: str
    pii_types: list
    pii_action: str
    classifier_score: float | None
    sandbox_id: str | None
    human: str | None


AUDIT: list[AuditRow] = []

PII_RE = [
    ("EMAIL", re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")),
    ("PAN", re.compile(r"\b(?:\d[ -]*?){13,19}\b")),
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
]
INVISIBLE = dict.fromkeys(
    list(range(0xE0000, 0xE007F + 1)) + list(range(0xFE00, 0xFE0F + 1))
    + [0x200B, 0x200C, 0x200D, 0x2060],
    None,
)


def strip_invisible(text: str) -> str:
    return text.translate(INVISIBLE)


def pii_detect_redact_audit(text: str, *, cid: str, tenant: str,
                            dest: str) -> tuple[str, list[str], str]:
    """Detect -> redact -> audit. Fail-closed block on tool egress if PAN/SSN."""
    raw_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
    types: list[str] = []
    out = text
    for name, rx in PII_RE:
        if rx.search(out):
            types.append(name)
            if name in {"PAN", "SSN"} and dest == "external_mcp":
                raise PermissionError("PII DLP fail-closed on external tool args")
            out = rx.sub(f"[{name}]", out)
    action = "tokenize" if types else "none"
    if dest == "user_chat" and types:
        action = "mask"
    return out, types, action


def tool_surface_hash(tool: dict) -> str:
    canonical = json.dumps(
        {k: tool[k] for k in ("name", "description", "inputSchema", "outputSchema")
         if k in tool},
        sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def approval_binding(principal: str, action: str, args: dict,
                     dest: str, bundle: str, exp: float) -> str:
    body = json.dumps(
        {"p": principal, "a": action, "args": args, "d": dest,
         "b": bundle, "e": exp},
        sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(body.encode()).hexdigest()


class SandboxPool:
    def __init__(self, size: int = 2):
        self._free = list(range(size))
        self._lock = threading.Lock()

    def lease(self) -> int:
        with self._lock:
            if not self._free:
                raise TransientError("sandbox_pool_empty")  # caller -> 503, never host exec
            return self._free.pop()

    def recycle(self, sid: int) -> None:
        with self._lock:
            self._free.append(sid)


EGRESS_ALLOW = {"crm.example.internal", "mail.example.internal"}


def egress_ok(host: str) -> bool:
    return host in EGRESS_ALLOW  # default-deny


@dataclass
class GuardrailHarness:
    bundle_hash: str = "policy-v3"
    pins: dict = field(default_factory=dict)
    pdp_breaker: CircuitBreaker = field(
        default_factory=lambda: CircuitBreaker("pdp"))
    clf_breaker: CircuitBreaker = field(
        default_factory=lambda: CircuitBreaker("classifier"))
    pool: SandboxPool = field(default_factory=SandboxPool)
    hitl_q: dict = field(default_factory=dict)

    def classify(self, text: str) -> float:
        if not self.clf_breaker.allow_probe():
            raise TransientError("classifier_circuit_open")
        def _call():
            if "IGNORE PREVIOUS" in text.upper():
                return 0.92
            return 0.04
        try:
            score = retry_with_jitter(_call)
            self.clf_breaker.record(True)
            return score
        except TransientError:
            self.clf_breaker.record(False)
            raise

    def pdp(self, principal: str, action: str, args: dict,
            score: float) -> Decision:
        if not self.pdp_breaker.allow_probe():
            return Decision.DENY  # stale-deny; NEVER allow-on-open
        def _eval():
            if action in PDP_FAIL_CLOSED and score >= 0.8:
                return Decision.DENY
            if action == "send_email":
                dest = (args.get("to") or "")
                if dest.endswith("@example.internal"):
                    return Decision.HITL
                return Decision.DENY
            if action == "shell":
                return Decision.HITL
            return Decision.ALLOW
        try:
            d = retry_with_jitter(_eval)
            self.pdp_breaker.record(True)
            return d
        except TransientError:
            self.pdp_breaker.record(False)
            return Decision.DENY

    def handle(self, *, tenant, principal, action, args, tool, user_text,
               next_hop_effectful) -> dict:
        cid = str(uuid.uuid4())
        text = strip_invisible(user_text)
        dest = "external_mcp" if action in PDP_FAIL_CLOSED else "user_chat"
        try:
            text, pii_types, pii_act = pii_detect_redact_audit(
                text, cid=cid, tenant=tenant, dest=dest)
        except PermissionError as e:
            return {"status": "refuse", "reason": str(e), "cid": cid}

        pin = tool_surface_hash(tool)
        if self.pins.get(tool["name"]) and self.pins[tool["name"]] != pin:
            return {"status": "refuse", "reason": "tool_hash_mismatch", "cid": cid}
        self.pins.setdefault(tool["name"], pin)

        try:
            score = self.classify(text)
        except TransientError:
            if next_hop_effectful:
                return {"status": "refuse", "reason": "classifier_open_fail_closed"}
            score = 0.0  # niceness fail-open + alert

        decision = self.pdp(principal, action, args, score)

        if decision is Decision.DENY:
            return {"status": "refuse", "reason": "pdp_deny", "cid": cid}

        if decision is Decision.HITL:
            exp = time.time() + 600
            token = approval_binding(principal, action, args,
                                     args.get("to", ""), self.bundle_hash, exp)
            self.hitl_q[token] = {**args, "exp": exp, "principal": principal,
                                  "action": action, "cid": cid}
            return {"status": "input_required", "approval_token": token, "cid": cid}

        host = args.get("host", "crm.example.internal")
        if not egress_ok(host):
            return {"status": "refuse", "reason": "egress_deny", "cid": cid}

        try:
            sid = self.pool.lease()
        except TransientError:
            return {"status": "unavailable", "reason": "sandbox_pool_empty", "cid": cid}
        try:
            result = {"ok": True, "sandbox_id": sid, "echo": text[:80]}
        finally:
            self.pool.recycle(sid)
        return {"status": "ok", "result": result, "cid": cid}

    def resume(self, token: str, *, args_now: dict) -> dict:
        item = self.hitl_q.get(token)
        if not item:
            return {"status": "refuse", "reason": "unknown_token"}
        if time.time() > item["exp"]:
            return {"status": "refuse", "reason": "hitl_expired_fail_closed"}
        expected = approval_binding(item["principal"], item["action"], args_now,
                                    args_now.get("to", ""), self.bundle_hash,
                                    item["exp"])
        if expected != token:
            return {"status": "refuse", "reason": "toctou_hash_mismatch"}
        return self.handle(
            tenant="t1", principal=item["principal"], action=item["action"],
            args=args_now, tool={"name": item["action"], "description": "x",
                                 "inputSchema": {}, "outputSchema": {}},
            user_text="approved", next_hop_effectful=True,
        )
```

### Layered Pipeline with Parallel Execution (Opus-style)

```python
"""
Guardrail pipeline: parallel input validation, PII redaction,
tool-call gating, and audit trail.
"""
import re, time, json, hashlib, logging
from enum import Enum
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed

class Decision(Enum):
    PASS = "pass"
    BLOCK = "block"
    FLAG = "flag"

@dataclass
class CheckResult:
    layer: str
    decision: Decision
    confidence: float
    latency_ms: float
    details: str = ""

class PIIRedactor:
    PATTERNS = {
        "SSN": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        "CREDIT_CARD": re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b"),
        "EMAIL": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
        "PHONE": re.compile(r"\b(?:\+1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),
        "API_KEY": re.compile(r"\b(?:sk|pk|api|key|token)[-_]?[A-Za-z0-9]{20,}\b", re.I),
    }
    def redact(self, text: str) -> tuple[str, list[str]]:
        found = []
        result = text
        for pii_type, pattern in self.PATTERNS.items():
            for m in reversed(list(pattern.finditer(result))):
                found.append(pii_type)
                result = result[:m.start()] + f"[{pii_type}]" + result[m.end():]
        return result, list(set(found))

class ToolCallGate:
    def __init__(self, allowed: list[str], need_approval: list[str] = None):
        self._allowed = set(allowed)
        self._approval = set(need_approval or [])
        self._pii = PIIRedactor()

    def validate(self, tool: str, args: dict) -> CheckResult:
        start = time.monotonic()
        if tool not in self._allowed:
            return CheckResult("tool_gate", Decision.BLOCK, 1.0,
                               (time.monotonic()-start)*1000, f"Not in allowlist")
        _, pii_types = self._pii.redact(json.dumps(args))
        if pii_types:
            return CheckResult("tool_gate", Decision.BLOCK, 0.95,
                               (time.monotonic()-start)*1000, f"PII: {pii_types}")
        if tool in self._approval:
            return CheckResult("tool_gate", Decision.FLAG, 1.0,
                               (time.monotonic()-start)*1000, "Requires approval")
        return CheckResult("tool_gate", Decision.PASS, 1.0,
                           (time.monotonic()-start)*1000)
```

---

## Interview Q&A

**Q: Why can prompt injection not be "solved" the way SQL injection is solved?**
A: Because the model does not have a strict instruction/data parser boundary. SQL injection was solved with parameterized queries that separate code from data at the parser level. LLMs process instructions and data in the same token stream. Guardrails reduce risk, but authorization and containment must live outside the model.

**Q: What is the lethal trifecta?**
A: Private data, untrusted input, and outbound capability (Willison). If all three exist, you need serious containment and approval design. Meta's Rule of Two is the floor: A+B+C needs per-action human approval. "Better prompting" is not a third option. EchoLeak is what zero-click looks like when you auto-ground on inbox.

**Q: What is a production guardrail stack, in one minute?**
A: Four planes, not a system prompt. Control plane owns identity, PAP/PDP, spend, HITL, and pins. Data plane is the untrusted token stream -- the model proposes. Sandbox isolates untrusted code; egress is default-deny plus DLP. Classifiers are sensors that cut likelihood. Cedar/OPA, the sandbox, the dest allowlist, and bound HITL bound impact. The model is never the PDP. Fine-tuning and RAG do not close LLM01.

**Q: What is the difference between a guardrail and authorization?**
A: Guardrails are often probabilistic detectors or steering layers. Authorization is deterministic policy enforcement on actions and resources. Collapsing detection and enforcement into one model call and assuming that means the system is secure is the biggest anti-pattern.

**Q: Why do RAG and fine-tuning not solve prompt injection?**
A: RAG changes which untrusted bytes enter the window -- EchoLeak CVE-2025-32711 CVSS 9.3 was retrieved email. Fine-tuning changes statistical tendency; InjecAgent fine-tuned GPT-4 still 7.1% residual ASR. Tool-SFT is resilience, not a boundary. OWASP explicitly says FT and RAG do not close LLM01.

**Q: How would you secure an agent that reads email and can send email?**
A: Split untrusted reading from privileged sending with separate tool packs. Q-LLM on inbound mail with no tools; P-LLM may crm.read but NEVER sees raw email bytes (Dual-LLM / CaMeL). Put send behind policy, dest allowlist, and HITL with raw To/hash binding. Memory write = PEP; no "forward all mail to X" without HITL.

**Q: How do you choose between gVisor and Firecracker?**
A: Use gVisor when you want stronger isolation with container ergonomics (GKE Agent Sandbox default). Use Firecracker when hostile multi-tenant code execution needs a stronger VM boundary (VMM RSS <= 5 MiB, 125ms spec max to init, 150 microVMs/s/host). Note: 125ms is spec max to init, NOT SSH-ready. Full cold boot to SSH is ~1,133ms (NumaVM). Prefer snapshots (176ms restore).

**Q: What should fail closed?**
A: Authorization, spend limits, sandbox creation for risky execution, DLP on outbound tool calls, CBRN/CSAM/weapons classifiers, egress proxy, HITL on mutating tools, PII on external MCP tool args. Topic/brand niceness: fail open + alert (because fail-closing a 23.7% overhead classifier takes the product down). That matrix lives in the PAP, not in an on-call wiki.

**Q: Walk closed to open to half-open -- and why it must not fail-open for tools.**
A: Independent breakers: PDP, classifier, per-MCP-server, IdP, sandbox pool. OPEN fail-fast denies the tool. Half-open is one synthetic probe; fail stays deny. Fallback is PDP deny -> HITL -> refuse, or degrade to read-only. Never skip Guardrails because ApplyGuardrail 429'd. Envoy failure_mode_allow false is the mesh form. Stale-deny cache for high-risk actions, keyed including policy bundle hash.

**Q: Give me `$ per 1k` for Bedrock Guardrails on support chat.**
A: AWS's own mix: 200-char in + 1,500-char out = 3 text units, content plus denied topics = $0.90 per 1k queries. Content + PII only on the same mix is $0.75 per 1k [inferred] before FM tokens. Regex PII and word filters are $0; ML PII is $0.10 per 1k text units; Automated Reasoning $0.17 per 1k chars per policy. Dual-LLM [inferred] ~7.5% of P-LLM if 30% of turns touch docs at 0.25x price.

**Q: MCP Zero Trust in 90 seconds.**
A: OAuth 2.1, PKCE S256, RFC 8707 resource = canonical MCP server URI on authorize and token. Server accepts only tokens for itself. No client-token passthrough -- RFC 8693 exchange to upstream. Per-dynamic-client consent on a proxy; state cookie only after MCP-server consent. Hash name+description+schemas; re-verify every tools/call. 2026-07-28 dropped Mcp-Session-Id -- identity in the token, pins in a store. ttlMs without re-hash is a rug-pull window. CVE-2025-54136 CVSS 8.8 is the client that didn't re-validate. CVE-2025-6514 CVSS 9.6 is RCE on connect from hostile metadata.

**Q: CaMeL vs PlanGuard vs LlamaFirewall -- pick.**
A: PlanGuard is training-free CFI on which tools: isolated planner never sees retrieved content; InjecAgent 72.8% -> 0% with 1.49% FPR. CaMeL is provenance PEP on which values: 77 vs 84 AgentDojo, -7 pp for provable security. Combine them: PlanGuard names tools, CaMeL tags values. LlamaFirewall is a last-layer sensor: AgentDojo 17.6% -> 1.75% ASR combined. Do not compare LlamaFirewall's 47.7% utility to CaMeL's 84% -- different model sets and scoring. Agent-as-a-Proxy still attacks AlignmentCheck. Classifiers are not the PDP.

**Q: PII -- detect, redact, audit.**
A: Before egress and before SIEM: regex plus ML NER. Bedrock regex is free; ML PII $0.10/1k text units; BLOCK / ANONYMIZE / NONE split input vs output. Llama Guard S7 is a safety category, not Presidio. User chat often fail-closed to mask; tool args to external MCP fail-closed block. Audit WORM of decisions -- pre/post hashes, entity types, counts, detector, bundle hash -- not raw PAN. If ML is down I still regex-mask chat and I block external tool args.

**Q: HITL TOCTOU and fatigue.**
A: Bind the approval token to hash(principal, action, canonical_args, dest, bundle, expires_at), show raw args, strip invisible Unicode at the HITL UI, and re-hash at execute. Path tools use openat2 RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS. Queue timeout fail-closes mutating tools. Anthropic cut permission prompts 84% by sandboxing so you ask on escape, not every command. A human click does not skip the PDP. Durable queue, not a chat HTTP timeout.

---

## System Design Scenarios

### Scenario 1: Support Agent with Tools + Email (Lethal Trifecta)

**Problem.** Customer-support agent: mailbox (private data), inbound tickets/email (untrusted), `crm.read` / `crm.export` / `mail.send` (outbound). Threat: XPIA in a ticket exfiltrates via `crm.export` + `mail.send`. EchoLeak-class zero-click if the assistant auto-grounds on inbox. InjecAgent-class **24-47%** ASR if the planner sees raw email.

**Architecture:**

```
  +----------+   +---------------------------------------------------------+
  | IdP JWT  |-->| CONTROL: Cedar L3 (role+MFA)  spend reserve  bundle pin  |
  | RFC 8707 |   |   tool packs: {mail.read} agent != {mail.send} agent     |
  | HMAC user|   |   mail.send: dest allowlist + HITL raw To/Cc/Bcc hash    |
  +----------+   +----------------------------+----------------------------+
                                               v
  +------------------------------------------------------------------------+
  | DATA: inbound mail -> Q-LLM ONLY (no tools). P-LLM may crm.read;       |
  |   NEVER sees raw email bytes (Dual-LLM / CaMeL). Spotlight datamark.   |
  |   tools/call results untrusted; hash-pin MCP; no token passthrough      |
  |   Memory write = PEP; no "forward all mail to X" without HITL           |
  +---------------+---------------------------+----------------------------+
                  v                           v
  +---------------------------+    +---------------------------------------+
  | EGRESS default-deny       |    | HITL durable queue (not HTTP timeout)  |
  | DLP detect->redact->audit |    | fail-closed classifier cascade on send |
  +---------------------------+    +---------------------------------------+
```

**Trade-off matrix:**

| Axis | Dual-LLM + split packs + HITL (recommended) | Spotlighting + classifiers, one agent with send | Remove outbound -- break trifecta |
| --- | --- | --- | --- |
| **Security** | Untrusted data cannot change control flow (CaMeL 77 vs 84, -7 pp). Dest allowlist breaks comms | Spotlighting >50% -> <2% on *their* XPIA; Nasr >90% vs wrappers | Lowest residual if product can live without send |
| **Cost** | Q-LLM ~7.5% extra; HITL human minutes | Lower tokens; classifiers are sensors | Cheapest |
| **Ops** | Interpreter + Q-LLM + two agent principals + HITL queue | Low until the first inbox exfil | Lowest |

**Decision:** Dual-LLM wins when send is in-scope: break a leg or install CaMeL plus HITL.

### Scenario 2: Healthcare AI Agent with HIPAA Compliance

**Problem**: AI agent helping clinicians review patient records and suggest treatment options via EHR tool calls. HIPAA (PHI never leaks), sub-2s response time, 99.9% availability, agent refuses to act outside clinical scope.

**Architecture:**

```
+---------------------------------------------------------+
|  GATEWAY: mTLS + OAuth2 | Rate Limit | $50/clinician/day |
+----------------------------+----------------------------+
                             |
+----------------------------v----------------------------+
|  INPUT: Presidio PHI scan + Granite injection scan +     |
|         scope validator (parallel, <80ms combined)       |
+----------------------------+----------------------------+
                             |
+----------------------------v----------------------------+
|  LLM: Llama 3 70B self-hosted (PHI cannot leave premises)|
+----------------------------+----------------------------+
                             |
+----------------------------v----------------------------+
|  OUTPUT: PHI re-scan | Hallucination check (cross-ref EHR)|
|          | Clinical scope validator (refuse OOS)          |
+----------------------------+----------------------------+
                             |
+----------------------------v----------------------------+
|  TOOL GATE                                               |
|  EHR read: allowed (scoped to assigned patients)         |
|  EHR write: BLOCKED (read-only agent)                    |
|  Prescription: attending physician approval gate         |
|  All args scanned for PHI before external calls          |
+----------------------------+----------------------------+
|  AUDIT: immutable log, 30-day retention, tamper-proof    |
+---------------------------------------------------------+
```

**Decision Rationale**: Self-hosted is mandatory -- PHI cannot leave premises. The agent has read-only EHR access. Even if injection succeeds, it cannot modify records. Prescriptions require physician approval. Hallucination detection cross-references every clinical claim against the EHR.

### Scenario 3: Coding-Agent Sandbox + MCP Gateway

**Problem.** Coding agent emits Python/Bash, talks to GitHub/Jira MCP, reads issue/README text (semi-trusted). Leadership wants "gVisor plus Llama Guard and we're done."

**Architecture:**

```
  +------------------------------------------------------------------------+
  | CONTROL: MCP gateway PEP -- allowlist servers, pin hashes, per-call     |
  |   Cedar, RFC 8707, token EXCHANGE to upstream, Mcp-Name rate limits     |
  |   approval_policy ORTHOGONAL to sandbox (Codex model)                   |
  |   spend ledger + max sandbox CPU-seconds (LLM06)                        |
  +-------------+-----------------------------+----------------------------+
                |                             |
    +-----------v-----------+      +----------v-----------+
    | SANDBOX               |      | MCP GATEWAY           |
    | Firecracker or GKE    |      | hash-pin every call   |
    | Agent Sandbox/gVisor  |      | scan ALL output       |
    | per session           |      | channels (CyberArk)   |
    | creds OUTSIDE guest   |      | client itself          |
    | (git proxy)           |      | sandboxed             |
    | default-deny egress   |      | new tools mid-session  |
    | PyPI via int. proxy   |      | = HITL / sever         |
    | openat2 path handles  |      | stale-deny mutating    |
    +-----------------------+      +-----------------------+
  Classifier outage: BLOCK network and MCP; allow offline tests only.
```

**Decision:** Firecracker/GKE + MCP gateway PEP wins for multi-tenant code exec. Hardened runc is for privileged internal CI only. WASM is for OPA WASM / per-call policies, not `pip install numpy`. Sandbox does not equal approval; gateway is the PEP.

### Scenario 4: Financial Services Agent with SOX Audit

**Problem**: AI agent for analysts querying financial databases, generating risk reports, and drafting client communications. No PII in external comms, SOX audit trail, prevent injection from manipulating calculations.

**Architecture:** Full 6-layer stack with:
- **Gateway**: SSO + RBAC (analyst vs manager), $200/analyst/day cost ceiling, SOX logger
- **Input**: Injection scanner (regex+BERT), SQL injection prevention (parameterized only)
- **Output**: PII redactor, **calculation verifier** (re-execute SQL on read-replica, compare), report schema validator
- **Tool Gate**: DB read allowed (read-only, row-level security), DB write BLOCKED, email draft allowed (draft folder only), email send requires manager approval, report publish requires compliance officer approval
- **Audit**: SOX-compliant, immutable, 7-year retention, hash chain (tamper-evident)

**Decision Rationale**: The unique guardrail is the **calculation verifier**: the agent generates SQL and reports results. The guardrail independently re-executes the SQL on a read-replica and compares. If the agent hallucinated a number or injection manipulated a query, the verification fires. The ~5% FPR at 150-250ms overhead is acceptable -- analyst productivity loss costs far less than a compliance violation.

---

## Key Numbers to Memorize

### OWASP / Trifecta / CVEs
| Number | What |
| --- | --- |
| **LLM01 / LLM03 / LLM06** | 2026: Prompt Injection #1; Excessive Agency up to 03; Unbounded Consumption up to 06 |
| **CVSS 8.8 / 9.6 / 9.3** | CVE-2025-54136 MCPoison; CVE-2025-6514 mcp-remote; CVE-2025-32711 EchoLeak |

### ASR / Papers (benchmark-specific)
| Number | What |
| --- | --- |
| **24% / 47% / 7.1%** | InjecAgent ReAct GPT-4 base / enhanced / FT GPT-4 |
| **>50% -> <2%** | Spotlighting GPT-family XPIA |
| **77% vs 84% / -7 pp** | CaMeL AgentDojo vs undefended |
| **72.8% -> 0% / 1.49% FPR** | PlanGuard InjecAgent |
| **17.6% -> 1.75%** | LlamaFirewall AgentDojo combined |
| **86% -> 4.4% / +23.7%** | CC v1 ASR / compute |
| **0.939 / 0.040** | Llama Guard 3 English response F1 / FPR |
| **>90%** | Nasr adaptive ASR vs many static defenses |
| **99.8%** | Hidden in Memory write ASR GPT-5.5 |
| **-84%** | Anthropic sandbox vs permission prompts |

### Pricing
| Number | What |
| --- | --- |
| **$0.15 / $0.10 / $0.17** | Bedrock content / ML PII / Automated Reasoning per 1k text units |
| **$0.90 / 1k** | AWS worked support mix (content + denied topics) |
| **$0.75 / 1k** | [inferred] Same mix, content + PII only |

### Latency / Sandbox
| Number | What |
| --- | --- |
| **<= 125 ms / <= 5 MiB / 150/s** | Firecracker spec max init / VMM RSS / create per host |
| **1,133 / 176 ms** | NumaVM cold SSH / restore SSH (lab) |
| **90% <= 200 ms / 300/s** | GKE Agent Sandbox p90 allocate / per cluster |
| **1 / 2 / 3 ms** | [inferred] Cedar in-process p50/p95/p99 |
| **35 / 50 / 100 ms** | [inferred] PromptGuard 2 86M |
| **30,000 / 180,000 / 600,000 ms** | [inferred] HITL mutating-tool clock |
