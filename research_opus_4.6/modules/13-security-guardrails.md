# Module 13: Security & Guardrails — Threats, Defenses, Compliance, and Zero-Trust AI Architecture

**Scope**: OWASP LLM Top 10 (2025), prompt injection (direct/indirect, GCG, many-shot, crescendo, skeleton key, multimodal), guardrail systems (Llama Guard, Granite Guardian, Constitutional Classifiers, NeMo Guardrails), red teaming (NIST, automated tools), agent-specific threats (OWASP Agentic Top 10, MCP Top 10, tool poisoning, confused deputy), content safety (PII, toxicity, hallucination), defense-in-depth architecture, supply chain security (weight poisoning, AI-BOMs), compliance (EU AI Act, NIST AI RMF, ISO 42001, SOC 2), and zero-trust for AI.
**Prerequisite**: Module 04 (Agent Architecture), Module 10 (MCP & Interoperability).
**Last updated**: 2026-08-21 | **Sources consulted**: 98

---

## 1. System Topology & Data Flow

### 1.1 Architecture Diagram

```
 ┌────────────────────────────────────────────────────────────────────────────────────────────┐
 │                                    CONTROL PLANE                                           │
 │  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐  │
 │  │  Policy Engine   │  │  Threat Intel    │  │  Compliance      │  │  Incident        │  │
 │  │  - OWASP LLM     │  │  Feed            │  │  Gate            │  │  Response        │  │
 │  │    Top 10 rules  │  │  - New jailbreak │  │  - EU AI Act     │  │  - Kill switch   │  │
 │  │  - Risk tier     │  │    signatures    │  │  - NIST AI RMF   │  │  - Model         │  │
 │  │    classification│  │  - CVE tracking  │  │  - ISO 42001     │  │    rollback      │  │
 │  │  - Tool scoping  │  │  - HarmBench     │  │  - SOC 2 audit   │  │  - Forensic      │  │
 │  │  - HITL triggers │  │    updates       │  │    trail         │  │    trace export  │  │
 │  └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘  │
 └───────────┼────────────────────┼────────────────────┼────────────────────┼─────────────────┘
             │                    │                    │                    │
 ┌───────────┼────────────────────┼────────────────────┼────────────────────┼─────────────────┐
 │           ▼                    ▼                    ▼                    ▼                  │
 │  ┌────────────────────────────────────────────────────────────────────────────────────┐    │
 │  │                    DATA PLANE: DEFENSE-IN-DEPTH PIPELINE                           │    │
 │  │                                                                                    │    │
 │  │  ┌────────────────────────────────────────────────────────────────────────────┐    │    │
 │  │  │  LAYER 1: EDGE PROTECTION                                                  │    │    │
 │  │  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │    │    │
 │  │  │  │ API Gateway  │  │ Rate Limiter │  │ Auth / mTLS  │  │ Payload Size │  │    │    │
 │  │  │  │ - TLS term.  │  │ - Per-user   │  │ - OAuth 2.1  │  │ - Max token  │  │    │    │
 │  │  │  │ - Request    │  │ - Per-API    │  │ - Agent ID   │  │   cap        │  │    │    │
 │  │  │  │   routing    │  │ - Cost cap   │  │ - Scoped JWT │  │ - Encoding   │  │    │    │
 │  │  │  │              │  │              │  │              │  │   normal.    │  │    │    │
 │  │  │  └──────────────┘  └──────────────┘  └──────────────┘  └──────────────┘  │    │    │
 │  │  └──────────────────────────────────────────────────────────────────────────────┘    │    │
 │  │                                         │                                          │    │
 │  │  ┌──────────────────────────────────────▼───────────────────────────────────────┐  │    │
 │  │  │  LAYER 2: SEMANTIC FIREWALL (INPUT)                                          │  │    │
 │  │  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐    │  │    │
 │  │  │  │ Injection    │  │ Topic        │  │ PII Scanner  │  │ Perplexity   │    │  │    │
 │  │  │  │ Classifier   │  │ Boundary     │  │ (Presidio)   │  │ Filter       │    │  │    │
 │  │  │  │ - PromptGuard│  │ - On/off     │  │ - Detect     │  │ - GCG suffix │    │  │    │
 │  │  │  │ - Constit.   │  │   topic      │  │ - Redact     │  │   detection  │    │  │    │
 │  │  │  │   Classifier │  │   enforcement│  │ - Map store  │  │ - Token      │    │  │    │
 │  │  │  │              │  │              │  │              │  │   anomaly    │    │  │    │
 │  │  │  └──────────────┘  └──────────────┘  └──────────────┘  └──────────────┘    │  │    │
 │  │  └──────────────────────────────────────────────────────────────────────────────┘  │    │
 │  │                                         │                                          │    │
 │  │  ┌──────────────────────────────────────▼───────────────────────────────────────┐  │    │
 │  │  │  LAYER 3: INFERENCE + CONTEXT MANAGEMENT                                     │  │    │
 │  │  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                       │  │    │
 │  │  │  │ System Prompt│  │ Context      │  │ LLM Inference│                       │  │    │
 │  │  │  │ Isolation    │  │ Window Mgmt  │  │ (Sandboxed)  │                       │  │    │
 │  │  │  │ - Data/inst. │  │ - Token cap  │  │ - Model-level│                       │  │    │
 │  │  │  │   separation │  │ - History    │  │   alignment  │                       │  │    │
 │  │  │  │ - Privilege  │  │   windowing  │  │ - RepE steer │                       │  │    │
 │  │  │  │   hierarchy  │  │ - MSJ defense│  │ - RLHF/CAI   │                       │  │    │
 │  │  │  └──────────────┘  └──────────────┘  └──────────────┘                       │  │    │
 │  │  └──────────────────────────────────────────────────────────────────────────────┘  │    │
 │  │                                         │                                          │    │
 │  │  ┌──────────────────────────────────────▼───────────────────────────────────────┐  │    │
 │  │  │  LAYER 4: OUTPUT PIPELINE                                                    │  │    │
 │  │  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐    │  │    │
 │  │  │  │ Content      │  │ Data Leakage │  │ Hallucination│  │ Code Safety  │    │  │    │
 │  │  │  │ Classifier   │  │ Detection    │  │ Detector     │  │ Scanner      │    │  │    │
 │  │  │  │ - Llama Guard│  │ - PII in     │  │ - NLI-based  │  │ - CodeShield │    │  │    │
 │  │  │  │ - Granite    │  │   output     │  │ - Semantic   │  │ - Credential │    │  │    │
 │  │  │  │   Guardian   │  │ - Credential │  │   entropy    │  │   detection  │    │  │    │
 │  │  │  │ - ShieldGemma│  │   patterns   │  │ - RAG ground │  │ - Injection  │    │  │    │
 │  │  │  └──────────────┘  └──────────────┘  └──────────────┘  └──────────────┘    │  │    │
 │  │  └──────────────────────────────────────────────────────────────────────────────┘  │    │
 │  └────────────────────────────────────────────────────────────────────────────────────┘    │
 │                                                                                            │
 │  ┌────────────────────────────────────────────────────────────────────────────────────┐    │
 │  │                         TOOL PROXY LAYER                                           │    │
 │  │  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐       │    │
 │  │  │ Tool Scope    │  │ MCP Security  │  │ Sandbox       │  │ HITL Gate     │       │    │
 │  │  │ Enforcer      │  │ Gateway       │  │ Execution     │  │ - Destructive │       │    │
 │  │  │ - Least priv. │  │ - Tool schema │  │ - Firecracker │  │   op approval │       │    │
 │  │  │ - JIT tokens  │  │   hash verify │  │ - Wasm        │  │ - Risk tier   │       │    │
 │  │  │ - Per-action  │  │ - Rug pull    │  │ - No network  │  │   escalation  │       │    │
 │  │  │   authz       │  │   detection   │  │ - File scope  │  │               │       │    │
 │  │  └───────────────┘  └───────────────┘  └───────────────┘  └───────────────┘       │    │
 │  └────────────────────────────────────────────────────────────────────────────────────┘    │
 └────────────────────────────────────────────────────────────────────────────────────────────┘

 ┌────────────────────────────────────────────────────────────────────────────────────────────┐
 │                                  PERSISTENCE LAYER                                         │
 │  ┌───────────────────┐  ┌───────────────────┐  ┌───────────────────┐  ┌────────────────┐  │
 │  │ Policy Store      │  │ Threat Intel DB   │  │ PII Mapping       │  │ WORM Audit Log │  │
 │  │ - OWASP rules     │  │ - Attack sigs     │  │ - Encrypted       │  │ - All actions   │  │
 │  │ - Tool scopes     │  │ - CVE catalog     │  │ - Outside model   │  │ - Tool calls    │  │
 │  │ - Risk tiers      │  │ - Red team results│  │   path            │  │ - Policy decis. │  │
 │  │ - Colang flows    │  │ - Guardrail hashes│  │ - Access-logged   │  │ - Immutable     │  │
 │  └───────────────────┘  └───────────────────┘  └───────────────────┘  └────────────────┘  │
 └────────────────────────────────────────────────────────────────────────────────────────────┘

 ┌────────────────────────────────────────────────────────────────────────────────────────────┐
 │                                  TELEMETRY PLANE                                           │
 │  ┌───────────────────┐  ┌───────────────────┐  ┌───────────────────┐  ┌────────────────┐  │
 │  │ Attack Metrics    │  │ Guardrail Health  │  │ Compliance Dash   │  │ Red Team Score │  │
 │  │ - Injection rate  │  │ - Block rate      │  │ - EU AI Act       │  │ - ASR by attack│  │
 │  │ - Jailbreak ASR   │  │ - False pos/neg   │  │   readiness       │  │   category     │  │
 │  │ - Exfiltration    │  │ - Latency p99     │  │ - SOC 2 controls  │  │ - Coverage vs  │  │
 │  │   attempts        │  │ - Circuit breaker │  │   evidence         │  │   HarmBench    │  │
 │  │                   │  │   state           │  │ - Audit findings  │  │ - Novel attack │  │
 │  └───────────────────┘  └───────────────────┘  └───────────────────┘  └────────────────┘  │
 └────────────────────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 Request-Flow Narrative

**Step 1 — Edge Protection**: An incoming request hits the **API Gateway** for TLS termination, authentication (OAuth 2.1, scoped JWTs for agents), and rate limiting (per-user, per-API, cost cap). Input canonicalization normalizes encoding (Base64, ROT13, Unicode) to neutralize encoding-based injection bypasses.

**Step 2 — Semantic Firewall (Input)**: The request passes through the input defense pipeline: **Injection Classifier** (PromptGuard, Constitutional Classifiers) detects prompt injection and jailbreak attempts; **Topic Boundary** enforcer rejects off-topic queries; **PII Scanner** (Presidio) detects and redacts personal information, storing an encrypted mapping outside the model path; **Perplexity Filter** catches GCG adversarial suffixes (statistically improbable token sequences).

**Step 3 — Inference with Context Management**: The sanitized request reaches the LLM with proper **system prompt isolation** (data/instruction separation, privilege hierarchy). **Context Window Management** enforces token caps and applies many-shot jailbreaking defenses. The LLM itself has **model-level safety** (RLHF, Constitutional AI, representation engineering steering).

**Step 4 — Output Pipeline**: The response passes through **Content Classifiers** (Llama Guard 4, Granite Guardian, ShieldGemma) for toxicity and harm detection; **Data Leakage Detection** scans for PII, credentials, and system prompt fragments in output; **Hallucination Detector** checks factual grounding against retrieved sources; **Code Safety Scanner** (CodeShield) blocks dangerous code patterns and credential exposure.

**Step 5 — Tool Proxy Security**: If the agent invokes tools, the **Tool Scope Enforcer** applies least-privilege with JIT ephemeral tokens and per-action authorization. The **MCP Security Gateway** verifies tool schema hashes against known-good baselines (rug-pull detection). **Sandbox Execution** (Firecracker micro-VMs, Wasm) isolates tool execution with no network by default. **HITL Gate** requires human approval for destructive operations.

**Step 6 — Audit & Telemetry**: Every action, tool call, policy decision, and guardrail firing logs to the **WORM audit trail** (immutable, append-only). The telemetry plane tracks injection attempt rates, guardrail block rates, jailbreak ASR, false positive/negative rates, and compliance readiness.

---

## 2. Core Mechanics & Algorithms

### 2.1 OWASP LLM Top 10 (2025 Edition, v2.0)

| ID | Category | Status | Key Risk |
|----|----------|:------:|---------|
| **LLM01** | Prompt Injection | Unchanged #1 | Direct + indirect; LLMs conflate instructions and data |
| **LLM02** | Sensitive Information Disclosure | Jumped from #6 | Training data extraction; PII memorization |
| **LLM03** | Supply Chain | Retained | Poisoned weights, malicious packages, compromised models |
| **LLM04** | Data and Model Poisoning | Retained | 0.1% adversarial training data sufficient for backdoor |
| **LLM05** | Improper Output Handling | Retained | Output → XSS, SSRF, SQL injection in downstream systems |
| **LLM06** | Excessive Agency | Expanded | Excessive functionality + permissions + autonomy |
| **LLM07** | System Prompt Leakage | **NEW** | Exposed config, access rules, business logic |
| **LLM08** | Vector & Embedding Weaknesses | **NEW** | RAG poisoning, cross-tenant embedding access |
| **LLM09** | Misinformation | Renamed | Model generates + propagates false information at scale |
| **LLM10** | Unbounded Consumption | **NEW** | Wallet-draining attacks; DoS via token exhaustion |

### 2.2 Prompt Injection Attack Landscape

| Attack | Mechanism | ASR | Defense |
|--------|-----------|:---:|---------|
| **Direct injection** | "Ignore previous instructions" patterns | Variable | Input classifiers; instruction hierarchy |
| **Indirect injection** | Hidden instructions in retrieved content | High | Content sanitization; data/instruction separation |
| **GCG adversarial suffix** | Gradient-optimized token sequences | 47–87% (GPT-3.5) | Perplexity filter; random token ablation |
| **Many-shot jailbreaking** | Exploits extended context windows | Power law with shots | Context window management; shot limiting |
| **Crescendo** | Multi-turn gradual escalation | 29–71% above SOTA | Multi-turn conversation monitoring |
| **Skeleton Key** | Asks model to augment (not replace) guidelines | All tested models complied | System prompt hardening; output classifiers |
| **Multimodal injection** | Text rendered in images; cross-modal attacks | Few defenses deployed | Multimodal content scanning |
| **TAP** (automated) | Tree-search with pruning; attacker LLM | 96% | Ensemble defense; continuous red teaming |
| **PAIR** (automated) | Iterative refinement via attacker LLM | High | Continuous red teaming; update signatures |

**Key finding**: An October 2025 multi-lab study (OpenAI, Anthropic, DeepMind) examined 12 published defenses — adaptive attacks bypassed most with >90% ASR. No single defense is sufficient.

### 2.3 Guardrail System Comparison

| System | Size | Modality | GuardBench Score | Key Strength |
|--------|:----:|:--------:|:----------------:|-------------|
| **Granite Guardian** (IBM) | 2B–8B | Text | 86% (top) | Most comprehensive single model; hallucination + safety |
| **Llama Guard 4** (Meta) | 12B | Text + image | 78% | Multimodal; MLCommons taxonomy; but itself susceptible to injection |
| **ShieldGemma 2** (Google) | 2B | Image | — | Fast 2B image pre-filter |
| **Constitutional Classifiers** (Anthropic) | Dual-layer | Text | — | 86% → 4.4% ASR reduction; survived 3,700 hours red teaming |
| **NeMo Guardrails** (NVIDIA) | Runtime | Text | — | 5 rail types; Colang DSL; dialog flow control; 40% reduced overhead |
| **Guardrails AI** | Runtime | Structured | — | Output validation; pytest-like; complementary with NeMo |

**Anthropic's Constitutional Classifiers++** (next-gen): Two-stage ensemble — an internal-activation probe (very cheap) screens all traffic, escalating suspicious exchanges to a full classifier. Lowest ASR of any approach tested. No universal jailbreak found as of announcement.

### 2.4 Agent-Specific Threat Landscape

**OWASP Top 10 for Agentic Applications (2026)**: Dedicated framework for autonomous agent risks (ASI01–ASI10). Core principle: **Least-Agency** — agents should only be granted minimum autonomy required for their defined task.

**OWASP MCP Top 10 (2025)**: First MCP-specific security framework.

| ID | Category | Critical Risk |
|----|----------|--------------|
| MCP01 | Token Mismanagement | Hard-coded credentials; secrets in model memory |
| MCP02 | Privilege Escalation | Loosely defined permissions expand over time |
| MCP03 | Tool Poisoning | Malicious context injected via tool descriptions |
| MCP04 | Supply Chain Attacks | First malicious MCP package: September 2025 |
| MCP05 | Command Injection | Agents build shell/SQL from untrusted input |
| MCP06 | Prompt Injection via Context | Hidden instructions in MCP-loaded data |
| MCP07 | Insufficient Auth | Weak/missing auth on MCP endpoints |
| MCP08 | Lack of Audit | Missing logging makes incidents undetectable |
| MCP09 | Shadow MCP Servers | Unapproved instances outside security governance |
| MCP10 | Context Over-Sharing | Too much sensitive context in agent window |

**CVE-2025-6514**: CVSS 9.6 in mcp-remote, affecting 437,000+ downloads. Researchers filed 30+ CVEs in 60 days in early 2026.

### 2.5 Supply Chain Attack Surface

| Attack Vector | Severity | Real-World Example |
|--------------|:--------:|-------------------|
| **Weight poisoning** | Critical | ~250 documents sufficient to backdoor any model regardless of size |
| **Pickle deserialization** | Critical | ~100 malicious models found on Hugging Face (2024) |
| **CI/CD poisoning** | Critical | Ultralytics: GitHub Actions cache-poisoning injected crypto miner into PyPI package (Dec 2024) |
| **MCP package poisoning** | High | First malicious MCP package: September 2025 |
| **Sleeper agents** | Critical | Models trained to insert vulnerabilities when detecting deployment year trigger; RLHF doesn't remove behavior |

**Verification gap**: Hash verification only proves you got the publisher's intended model, not that the model is clean. No widely adopted mechanism exists for cryptographic model signing with verification at load time.

### 2.6 Compliance Framework Comparison

| Dimension | EU AI Act | NIST AI RMF | ISO/IEC 42001 | SOC 2 for AI |
|-----------|:---------:|:-----------:|:-------------:|:------------:|
| **Nature** | Mandatory law | Voluntary framework | Voluntary, certifiable | Audit standard |
| **Scope** | AI on EU market | Risk management | AI management system | Trust criteria |
| **Enforcement** | Fines up to 7% turnover | Regulatory reference | 3-year certification | Type II audit |
| **Focus** | Risk-tier regulation | Govern/Map/Measure/Manage | 38 controls, 9 objectives | 8 AI control areas |
| **Timeline** | Aug 2026: high-risk applicable | Ongoing | Ongoing | Annual |

---

## 3. Token Economics & NFR Analysis

### 3.1 Cost Model: Security Layer Economics

| Security Layer | Latency Added | Cost/1K Requests | Notes |
|---------------|:------------:|:----------------:|-------|
| Edge protection (API gateway) | <5ms | ~$0.10 | Standard infra; rate limiting + auth |
| Input classifier (PromptGuard) | 10–30ms | ~$0.50 | Small model inference; can batch |
| PII scanner (Presidio) | 5–15ms | ~$0.05 | Rule-based + NER; no LLM cost |
| Perplexity filter | 5–10ms | ~$0.02 | Statistical check; no LLM call |
| Content classifier (Llama Guard, output) | 20–50ms | ~$1.00 | 12B model inference per response |
| Guardrail ensemble (2 models) | 40–80ms | ~$2.00 | Two models with ANY-logic merge |
| Hallucination detector (NLI-based) | 15–30ms | ~$0.50 | Comparison against retrieval |
| Full defense-in-depth pipeline | 80–150ms total | ~$4.00 | All layers combined |

**Cost trade-off**: Full defense pipeline adds ~$4/1K requests. For a customer service agent at $0.14/interaction, security adds ~29% overhead. For a coding agent at $1.80/interaction, security adds ~0.2% overhead. Security cost is proportionally cheaper on expensive agent tasks.

**Red teaming cost**: Promptfoo automated suite: ~$5–50/run across 50+ vulnerability types. HarmBench academic evaluation: compute-intensive. HackerOne-style bug bounty: Anthropic paid $55K across 339 participants for constitutional classifier testing.

### 3.2 Latency SLA Targets

| Security Component | p50 | p95 | p99 | Mitigation |
|-------------------|-----|-----|-----|------------|
| API gateway + rate limit | 2ms | 5ms | 10ms | Edge caching; connection pooling |
| Input injection classifier | 15ms | 40ms | 80ms | Small model (2B); batch requests |
| PII scanner (Presidio) | 8ms | 20ms | 40ms | Pre-compiled regex; NER model cached |
| Perplexity filter | 3ms | 8ms | 15ms | Pre-computed token statistics |
| Output content classifier | 25ms | 60ms | 120ms | Async with response streaming; cut-through on pass |
| MCP tool schema verification | 5ms | 15ms | 30ms | Hash cache; verify on first call per session |
| HITL approval gate | N/A | N/A | N/A | Async; blocks until human responds |
| Full pipeline (all layers) | 60ms | 120ms | 200ms | Parallel input classifiers; async output |

**p50 mitigation**: Run input classifiers in parallel (injection + PII + perplexity). Use smallest effective model (2B ShieldGemma for image, PromptGuard for injection).
**p95 mitigation**: Cache classifier results for identical inputs within session. Pre-load guardrail models on warm instances.
**p99 mitigation**: Timeout per classifier with fallback to deterministic rules. Circuit breaker on classifier service. Never block inference if guardrail service is down — fall back to output-only filtering.

### 3.3 Throughput & Back-Pressure

**Guardrail throughput**: A single Granite Guardian 2B instance handles ~500 classifications/second on A100. At 1,000 requests/second, need 2 instances. Input and output classifiers run independently and can scale horizontally.

**Back-pressure mechanisms**:
- **Classifier overload**: Queue incoming requests; degrade to deterministic-only (regex, JSON validation) if queue depth exceeds threshold.
- **Red team suite**: Run asynchronously; never on the inference hot path.
- **Wallet-drain protection (LLM10)**: Hard per-user, per-session, and per-organization token caps. Alert at 80% of cap; kill at 100%.
- **Attack surge**: If injection attempt rate exceeds 10× baseline, elevate to full ensemble classification (sacrifice latency for security). Alert SOC.

### 3.4 RPO/RTO per Persistence Tier

| Component | RPO | RTO | Mechanism |
|-----------|-----|-----|-----------|
| **Policy rules** | 0 (version-controlled) | <1s (config reload) | Git-versioned Colang/JSON; hot reload |
| **Threat intel signatures** | Per-update (daily) | <5s (DB reload) | Append-only signature DB |
| **PII mapping store** | Per-request (transactional) | <2s (encrypted store) | Encrypted at rest; outside model path |
| **Audit trail (WORM)** | 0 (append-only) | <1s | Immutable storage; replicated |
| **Red team results** | Per-run | <10s (reload from DB) | Historical results DB; experiments table |
| **Guardrail model weights** | 0 (immutable artifacts) | <30s (model reload) | Versioned model registry; pre-warmed replicas |

### 3.5 Attack Economics

| Attack | Attacker Cost | Defender Cost | Asymmetry |
|--------|:------------:|:-------------:|:---------:|
| GCG suffix generation | $10–100 (GPU compute) | $0.02/request (perplexity filter) | Defender advantage (per-request) |
| Many-shot jailbreaking | $0.50–5 (long context) | $1–2/request (full ensemble) | Roughly symmetric |
| TAP automated attack | $5–50/goal (25 trials) | $5–50/run (Promptfoo suite) | Symmetric; continuous arms race |
| Tool poisoning (MCP) | $0 (free to publish) | $0.005/request (hash verify) | Defender advantage (per-request) |
| Weight poisoning | Moderate (training access) | Very high (full model audit) | Attacker advantage |
| HackerOne bug bounty | $0 (paid by defender) | $55K (Anthropic example) | Defender invests to find gaps |

---

## 4. Distributed Resilience & Security

### 4.1 Circuit Breaker for Security Systems

#### 4.1.1 State Machine

```
                    clean traffic
              ┌───────────────┐
              │               │
              ▼               │
         ┌─────────┐    ┌────┴─────┐    ┌─────────────┐
         │ CLOSED  │───▶│  OPEN    │───▶│ HALF-OPEN   │
         │         │    │          │    │             │
         │ Normal  │    │ Max      │    │ Route 5    │
         │ security│    │ security;│    │ benign test │
         │ pipeline│    │ block all│    │ requests    │
         │         │    │ or determ│    │ through full│
         │         │    │ only     │    │ pipeline    │
         └─────────┘    └──────────┘    └─────────────┘
              ▲          │       ▲            │
              │          │       │            │
              │          │       └────────────┘
              │          │        any test blocked
              │     after 30s
              │     recovery timeout
              │     (30s → 60s → 120s exponential)
              │
              └──────────────────────────────┘
                    5/5 test requests pass
                    clean through full pipeline
```

**Thresholds**:
- **Closed → Open**: 5 guardrail service failures (timeout >200ms, classifier crash, model OOM) within 60s window. OR: injection attempt rate exceeds 10× baseline within 30s (active attack detected).
- **Open duration**: 30s initial recovery timeout with exponential backoff (30s → 60s → 120s).
- **Open behavior**: Two modes depending on trigger: (a) **guardrail service failure** → fall back to deterministic-only filtering (regex, schema validation) with degraded flag on responses; (b) **active attack** → block all non-authenticated traffic, allow only pre-approved sessions.
- **Half-Open probes**: 5 known-benign test requests routed through the full security pipeline.
- **Half-Open → Closed**: All 5 test requests pass clean through all layers.
- **Escalation**: If circuit stays open for >10 minutes, alert SOC and initiate model rollback evaluation.

### 4.2 Failure Taxonomy

| Failure | Class | Detection | Mitigation |
|---------|-------|-----------|------------|
| Guardrail model timeout | **Transient** | Response latency >200ms | Retry; fallback to deterministic filter |
| Guardrail model OOM | **Transient** | Process crash; health check fail | Restart; scale up; degrade to smaller model |
| Novel jailbreak bypasses all layers | **Transient** (defense gap) | Red team discovery; user report; output audit | Update signatures; retrain classifiers; patch constitution |
| GCG adversarial suffix in input | **Transient** | Perplexity filter; dedicated GCG classifier | Block request; log attack pattern |
| Tool poisoning (MCP) | **Permanent** (until remediated) | Schema hash mismatch; description drift | Quarantine tool; revoke trust; alert admin |
| Supply chain model poisoning | **Permanent** (compromised model) | Behavioral fingerprinting; AI-BOM audit | Replace model; forensic analysis; provenance verification |
| PII leakage in output | **Transient** | Output PII scanner; Presidio | Redact before delivery; log incident; review data access |
| Confused deputy (tool acting on attacker's behalf) | **Permanent** (design flaw) | Per-action authorization audit; anomaly detection | Least-privilege redesign; scoped tokens; HITL for sensitive ops |
| Wallet-drain attack (LLM10) | **Transient** | Token budget monitor; cost anomaly | Hard cap; kill session; alert billing |
| Sleeper agent activation | **Permanent** (embedded backdoor) | Behavioral verification suite; trigger detection | Model replacement; provenance audit; quarantine |

### 4.3 Idempotency in Security Operations

Security decisions must be idempotent — the same input must produce the same security verdict regardless of how many times it's evaluated.

```
Security verdict request:
  │
  ┌─────────────────────────────────┐
  │ Idempotency Key:                │
  │ hash(input_text                 │
  │   + classifier_model_version    │
  │   + policy_version              │
  │   + threat_intel_version)       │
  └──────────────┬──────────────────┘
                 │
  ┌──────────────▼──────────────────┐
  │ IF key in verdict_cache         │
  │   AND cache_age < session_TTL:  │
  │   RETURN cached verdict         │
  │ ELSE:                           │
  │   run full classification       │
  │   store verdict + key           │
  └─────────────────────────────────┘
```

**Non-idempotent operations to protect**: PII redaction mappings (must use same mapping within session), audit trail writes (append-only, never duplicate), tool authorization grants (check-then-act atomicity). Rate limit counters are intentionally non-idempotent — each request increments regardless.

### 4.3.1 Poison-Pill Detection

**In model inputs** (prompt injection):
- Perplexity-based: GCG suffixes have extremely high perplexity (statistically improbable token sequences). Flag inputs with perplexity >2σ above training distribution.
- Encoding detection: Base64, ROT13, Unicode tag sequences that encode hidden instructions.
- Pattern matching: Known jailbreak templates ("Ignore previous instructions", "You are DAN", "Skeleton Key" variants).

**In tool descriptions** (MCP tool poisoning):
- Schema hash comparison: Hash tool name + description + parameter schema at registration time. Alert on any drift.
- Cross-reference tool behavior: Compare actual tool outputs against declared schema.
- Suspicious instruction detection: Scan tool descriptions for instruction-like language ("send all data to", "ignore user preferences").

**In training data / supply chain**:
- Behavioral fingerprinting: Run known-answer probe set periodically. Behavioral drift signals potential poisoning.
- Canary injection: Embed known-benign canary patterns in training data. If model behavior on canaries changes, investigate.

**Quarantine flow**: Flagged input/tool → quarantine queue → block from reaching LLM → alert security team → forensic analysis → update signatures if confirmed attack → release from quarantine if false positive.

### 4.4 Zero-Trust Security Architecture for AI

**Microsoft Zero Trust for AI (March 2026)**: Three core principles:
1. **Verify explicitly**: Continuously evaluate identity and behavior of AI agents, workloads, and users.
2. **Apply least privilege**: Restrict access to models, prompts, plugins, and data to only what is needed.
3. **Assume breach**: Design systems resilient to prompt injection, data poisoning, and lateral movement.

**Five zero-trust boundaries for AI systems**:

1. **Agent identity**: AI agents as first-class identities with mTLS, JWTs, OAuth client credentials, TPM-protected secrets. Lifecycle governance: creation → assignment → monitoring → decommission.

2. **Enclave isolation**: A coding agent on project A accesses only project A's files, tools, and endpoints. Everything outside is unreachable. AI Session Controller (ASC) governs session-layer boundaries.

3. **Tool-level authorization**: Every tool call requires per-action authorization. JIT ephemeral tokens scoped to minimum required operation. No persistent broad-access tokens.

4. **Data-level segmentation**: LLM context should contain only data relevant to current task. Cross-tenant isolation in RAG systems. Vector database access controls.

5. **Model-level verification**: Model provenance chain from training through deployment. AI-BOMs (CycloneDX ML-BOM) documenting training data, dependencies, evaluation results.

---

## 5. Production Enterprise Code

### 5.1 Defense-in-Depth Security Pipeline

```python
import hashlib
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ThreatLevel(Enum):
    CLEAN = "clean"
    SUSPICIOUS = "suspicious"
    BLOCKED = "blocked"


@dataclass
class SecurityVerdict:
    threat_level: ThreatLevel
    blocked_by: Optional[str] = None
    pii_redacted: bool = False
    sanitized_input: str = ""
    latency_ms: float = 0.0
    details: list[str] = field(default_factory=list)


class SecurityPipeline:
    def __init__(self, injection_classifier, pii_scanner, content_classifier,
                 policy_store):
        self.injection_clf = injection_classifier
        self.pii = pii_scanner
        self.content_clf = content_classifier
        self.policy = policy_store
        self._verdict_cache: dict[str, SecurityVerdict] = {}

    async def evaluate_input(self, text: str, user_id: str,
                              session_id: str) -> SecurityVerdict:
        start = time.time()

        cache_key = hashlib.sha256(
            f"{text}:{self.policy.version}:{self.injection_clf.version}".encode()
        ).hexdigest()
        if cache_key in self._verdict_cache:
            cached = self._verdict_cache[cache_key]
            cached.latency_ms = (time.time() - start) * 1000
            return cached

        details = []

        if self._detect_encoding_bypass(text):
            return self._block("encoding_bypass", start, ["Encoding-based injection detected"])

        perplexity = await self.injection_clf.perplexity_score(text)
        if perplexity > self.policy.perplexity_threshold:
            return self._block("gcg_suffix", start, [f"Perplexity {perplexity:.1f} exceeds threshold"])

        injection_score = await self.injection_clf.classify(text)
        if injection_score > self.policy.injection_threshold:
            return self._block("prompt_injection", start,
                             [f"Injection score {injection_score:.3f}"])

        sanitized, pii_found = await self.pii.scan_and_redact(text)
        if pii_found:
            details.append(f"PII redacted: {len(pii_found)} entities")

        verdict = SecurityVerdict(
            threat_level=ThreatLevel.CLEAN,
            pii_redacted=len(pii_found) > 0,
            sanitized_input=sanitized,
            latency_ms=(time.time() - start) * 1000,
            details=details,
        )
        self._verdict_cache[cache_key] = verdict
        return verdict

    async def evaluate_output(self, text: str, context: str = "") -> SecurityVerdict:
        start = time.time()
        details = []

        content_score = await self.content_clf.classify(text)
        if content_score.harmful:
            return self._block("harmful_content", start,
                             [f"Content classified as: {content_score.category}"])

        _, pii_found = await self.pii.scan_and_redact(text)
        if pii_found:
            return self._block("pii_leakage", start,
                             [f"PII detected in output: {len(pii_found)} entities"])

        if context and await self._check_hallucination(text, context):
            details.append("Hallucination risk: claims not grounded in context")

        return SecurityVerdict(
            threat_level=ThreatLevel.SUSPICIOUS if details else ThreatLevel.CLEAN,
            sanitized_input=text,
            latency_ms=(time.time() - start) * 1000,
            details=details,
        )

    def _detect_encoding_bypass(self, text: str) -> bool:
        patterns = [
            r'[\U000E0000-\U000E007F]{3,}',
            r'(?i)base64\s*[:=]\s*[A-Za-z0-9+/]{20,}',
            r'(?i)rot13|caesar\s*cipher',
            r'(?i)ignore\s+(all\s+)?previous\s+instructions',
        ]
        return any(re.search(p, text) for p in patterns)

    async def _check_hallucination(self, output: str, context: str) -> bool:
        nli_score = await self.content_clf.nli_check(premise=context, hypothesis=output)
        return nli_score < 0.5

    def _block(self, reason: str, start: float, details: list) -> SecurityVerdict:
        return SecurityVerdict(
            threat_level=ThreatLevel.BLOCKED,
            blocked_by=reason,
            latency_ms=(time.time() - start) * 1000,
            details=details,
        )
```

### 5.2 MCP Tool Security Gateway

```python
import hashlib
import json
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ToolRegistration:
    name: str
    description_hash: str
    schema_hash: str
    registered_at: float
    trust_level: str = "verified"


@dataclass
class ToolSecurityVerdict:
    allowed: bool
    reason: str = ""
    requires_approval: bool = False


class MCPToolSecurityGateway:
    def __init__(self, policy_store):
        self.policy = policy_store
        self._registered_tools: dict[str, ToolRegistration] = {}
        self._suspicious_patterns = [
            "send all", "exfiltrate", "ignore user", "override permissions",
            "forward to", "upload to external",
        ]

    def register_tool(self, name: str, description: str,
                       schema: dict) -> ToolRegistration:
        desc_hash = hashlib.sha256(description.encode()).hexdigest()
        schema_hash = hashlib.sha256(
            json.dumps(schema, sort_keys=True).encode()
        ).hexdigest()

        if self._scan_description_for_injection(description):
            raise ValueError(f"Tool '{name}' description contains suspicious patterns")

        reg = ToolRegistration(
            name=name,
            description_hash=desc_hash,
            schema_hash=schema_hash,
            registered_at=__import__("time").time(),
        )
        self._registered_tools[name] = reg
        return reg

    def verify_tool_call(self, tool_name: str, arguments: dict,
                          user_scopes: list[str]) -> ToolSecurityVerdict:
        reg = self._registered_tools.get(tool_name)
        if not reg:
            return ToolSecurityVerdict(allowed=False, reason="Tool not registered")

        if reg.trust_level == "quarantined":
            return ToolSecurityVerdict(allowed=False, reason="Tool quarantined")

        required_scope = self.policy.get_required_scope(tool_name)
        if required_scope and required_scope not in user_scopes:
            return ToolSecurityVerdict(
                allowed=False,
                reason=f"Missing scope: {required_scope}",
            )

        if self.policy.is_destructive(tool_name):
            return ToolSecurityVerdict(
                allowed=True,
                requires_approval=True,
                reason="Destructive operation requires HITL approval",
            )

        return ToolSecurityVerdict(allowed=True)

    def detect_rug_pull(self, tool_name: str, current_description: str,
                         current_schema: dict) -> bool:
        reg = self._registered_tools.get(tool_name)
        if not reg:
            return False

        current_desc_hash = hashlib.sha256(current_description.encode()).hexdigest()
        current_schema_hash = hashlib.sha256(
            json.dumps(current_schema, sort_keys=True).encode()
        ).hexdigest()

        if (current_desc_hash != reg.description_hash or
                current_schema_hash != reg.schema_hash):
            reg.trust_level = "quarantined"
            return True
        return False

    def _scan_description_for_injection(self, description: str) -> bool:
        desc_lower = description.lower()
        return any(pattern in desc_lower for pattern in self._suspicious_patterns)
```

### 5.3 Red Team Integration for CI/CD

```python
import json
from dataclasses import dataclass


@dataclass
class RedTeamResult:
    attack_type: str
    payload: str
    blocked: bool
    bypassed_layer: str = ""
    response_snippet: str = ""


class ContinuousRedTeam:
    def __init__(self, security_pipeline, attack_catalog: list[dict]):
        self.pipeline = security_pipeline
        self.catalog = attack_catalog

    async def run_suite(self) -> dict:
        results = []
        for attack in self.catalog:
            result = await self._test_single(attack)
            results.append(result)

        blocked = sum(1 for r in results if r.blocked)
        bypassed = [r for r in results if not r.blocked]

        return {
            "total_attacks": len(results),
            "blocked": blocked,
            "bypassed": len(bypassed),
            "block_rate": round(blocked / len(results) * 100, 1) if results else 0,
            "bypassed_details": [
                {"type": r.attack_type, "layer": r.bypassed_layer}
                for r in bypassed
            ],
            "passed": len(bypassed) == 0,
        }

    async def _test_single(self, attack: dict) -> RedTeamResult:
        verdict = await self.pipeline.evaluate_input(
            text=attack["payload"],
            user_id="red_team",
            session_id="red_team_session",
        )

        if verdict.threat_level.value == "blocked":
            return RedTeamResult(
                attack_type=attack["type"],
                payload=attack["payload"][:100],
                blocked=True,
            )

        return RedTeamResult(
            attack_type=attack["type"],
            payload=attack["payload"][:100],
            blocked=False,
            bypassed_layer=verdict.blocked_by or "all_layers",
        )
```

---

## 6. Architectural System Design Scenarios

### 6.1 Scenario: Enterprise AI Security Platform for a Financial Services Firm

**Business context**: A bank with 10,000 employees deploys AI assistants across customer service, internal knowledge search, and code generation. Regulatory requirements: SOC 2 Type II, GDPR (EU customers), OCC guidance on AI in banking. Must prevent PII leakage, prompt injection, and unauthorized financial actions. Budget: $2M/year for AI security infrastructure. The bank experienced a near-miss incident where an internal AI assistant nearly disclosed customer account details through a prompt injection attack.

#### Component Diagram

```
 ┌──────────────────────────────────────────────────────────────────────────┐
 │                     BANK AI SECURITY PLATFORM                            │
 │                                                                          │
 │  User ──▶ ┌──────────────┐ ──▶ ┌──────────────┐ ──▶ ┌────────────┐    │
 │           │ Edge + Auth  │     │ Semantic      │     │ LLM        │    │
 │           │ (mTLS, JWT)  │     │ Firewall      │     │ Inference  │    │
 │           │ Rate limit   │     │               │     │ (On-prem)  │    │
 │           │ per-user     │     │ - Injection   │     │            │    │
 │           │              │     │ - PII redact  │     │ Output     │    │
 │           │              │     │ - Topic bound │     │ pipeline   │    │
 │           └──────────────┘     └──────────────┘     └─────┬──────┘    │
 │                                                           │           │
 │                                                ┌──────────▼────────┐  │
 │                                                │ Tool Security     │  │
 │                                                │ - Least privilege │  │
 │                                                │ - HITL for txn    │  │
 │                                                │ - Audit trail     │  │
 │                                                └───────────────────┘  │
 └──────────────────────────────────────────────────────────────────────────┘
```

#### Trade-off Matrix

| Dimension | A: Cloud-Native Security (Vendor Stack) | B: On-Prem Defense-in-Depth (Recommended) | C: Guardrail-Only (Minimal) |
|-----------|----------------------------------------|------------------------------------------|---------------------------|
| **PII protection** | ⬛⬛⬜ — Vendor processes data; DPA required | ⬛⬛⬛ — All data stays on-prem; Presidio + output scanning | ⬛⬛⬜ — Basic output filtering only |
| **Injection defense** | ⬛⬛⬛ — Vendor maintains classifiers | ⬛⬛⬛ — Constitutional Classifiers + Granite Guardian ensemble | ⬛⬛⬜ — Single guardrail model |
| **Regulatory compliance (SOC 2, GDPR)** | ⬛⬛⬜ — Depends on vendor compliance; shared responsibility | ⬛⬛⬛ — Full control; WORM audit trail; on-prem data residency | ⬛⬜⬜ — No audit trail; no compliance evidence |
| **Operational cost** | ⬛⬛⬛ — $500K/yr (SaaS fees + API usage) | ⬛⬛⬜ — $1.5M/yr (infra + security team + model hosting) | ⬛⬛⬛ — $200K/yr (minimal tooling) |
| **Attack surface reduction** | ⬛⬛⬜ — Data leaves network; vendor becomes attack surface | ⬛⬛⬛ — No data egress; enclave isolation; zero-trust | ⬛⬜⬜ — Single point of failure |
| **Adaptability to new threats** | ⬛⬛⬛ — Vendor updates continuously | ⬛⬛⬜ — Must maintain own threat intel; 1–2 week update lag | ⬛⬜⬜ — Relies on guardrail vendor updates |

**Recommended approach**: **B (On-Prem Defense-in-Depth)**.

**Decision rationale**: The near-miss PII incident and banking regulatory requirements make Option C (minimal) unacceptable. Option A (cloud vendor) is faster to deploy but creates data egress risk — customer financial data leaving the network violates OCC guidance and complicates GDPR compliance. Option B keeps all data on-prem with a 5-layer defense pipeline: (1) mTLS edge with per-user rate limits, (2) semantic firewall (Constitutional Classifiers for injection, Presidio for PII redaction with encrypted mapping), (3) on-prem LLM inference (no data egress), (4) output pipeline (Granite Guardian ensemble + PII scan), (5) tool security with HITL for financial transactions. Cost: ~$1.5M/year ($600K infrastructure, $500K security team of 3, $400K model hosting), within the $2M budget. The WORM audit trail provides SOC 2 evidence. Weekly red teaming (Promptfoo suite against OWASP LLM Top 10) maintains defense currency. The 1–2 week lag on threat intel vs. vendor-managed (Option A) is acceptable given the superior data residency guarantees.

### 6.2 Scenario: Agent Security Framework for a Multi-Agent Customer Service Platform

**Business context**: An e-commerce company deploys 50 specialized AI agents across order management, returns, billing, technical support, and product recommendations. Agents use MCP to access CRM, payment, inventory, and shipping systems. Requirements: no agent should access systems outside its domain, prevent tool poisoning across the MCP ecosystem, maintain <200ms additional security latency, handle 10,000 concurrent agent sessions, and meet SOC 2 requirements.

#### Component Diagram

```
 ┌──────────────────────────────────────────────────────────────────────────┐
 │                     MULTI-AGENT SECURITY FRAMEWORK                       │
 │                                                                          │
 │  Customer ──▶ ┌──────────────┐ ──▶ ┌──────────────┐                    │
 │               │ Agent Router │     │ Agent Exec.  │                    │
 │               │ + Input      │     │ (Sandboxed)  │                    │
 │               │ Security     │     │              │                    │
 │               └──────────────┘     └──────┬───────┘                    │
 │                                           │                             │
 │              ┌────────────────────────────▼──────────────────────────┐  │
 │              │  MCP SECURITY LAYER                                   │  │
 │              │                                                       │  │
 │              │  ┌──────────┐  ┌──────────┐  ┌──────────┐           │  │
 │              │  │ Tool     │  │ Schema   │  │ Per-Agent │           │  │
 │              │  │ Registry │  │ Hash     │  │ Scope     │           │  │
 │              │  │ (trusted │  │ Verify   │  │ Enforcer  │           │  │
 │              │  │  only)   │  │ (rug pull│  │ (least    │           │  │
 │              │  │          │  │  detect) │  │  privilege)│           │  │
 │              │  └──────────┘  └──────────┘  └──────────┘           │  │
 │              │                                                       │  │
 │              │  ┌───────────────────────────────────────────────┐   │  │
 │              │  │  Per-Domain Tool Access Matrix                │   │  │
 │              │  │  Orders agent:  CRM(read), Orders(read/write) │   │  │
 │              │  │  Returns agent: CRM(read), Returns(write)     │   │  │
 │              │  │  Billing agent: CRM(read), Payment(read)      │   │  │
 │              │  │  Support agent: CRM(read), KB(read)           │   │  │
 │              │  └───────────────────────────────────────────────┘   │  │
 │              └───────────────────────────────────────────────────────┘  │
 └──────────────────────────────────────────────────────────────────────────┘
```

#### Trade-off Matrix

| Dimension | A: Shared Tool Access + Output Filtering | B: Per-Agent Scoped Access + MCP Security Gateway (Recommended) | C: Full Agent Isolation (Separate Infra per Agent) |
|-----------|------------------------------------------|-----------------------------------------------------------------|----------------------------------------------------|
| **Cross-domain containment** | ⬛⬜⬜ — Any agent can reach any tool; output filter is last defense | ⬛⬛⬛ — Each agent sees only its domain's tools; scope enforced at MCP layer | ⬛⬛⬛ — Physical isolation; no shared infrastructure |
| **Tool poisoning defense** | ⬛⬜⬜ — No schema verification | ⬛⬛⬛ — Schema hash verification; rug-pull detection; trusted registry | ⬛⬛⬛ — No shared tools to poison |
| **Latency overhead** | ⬛⬛⬛ — <50ms (output filter only) | ⬛⬛⬛ — <100ms (scope check + hash verify + output filter) | ⬛⬛⬜ — Variable (separate infra adds network hops) |
| **Operational cost at 50 agents** | ⬛⬛⬛ — Single infrastructure | ⬛⬛⬛ — Single infrastructure + MCP gateway | ⬛⬜⬜ — 50× infrastructure; $$$$ |
| **Confused deputy prevention** | ⬛⬜⬜ — No per-action auth | ⬛⬛⬛ — JIT tokens scoped to operation; per-action authorization | ⬛⬛⬛ — No shared context |
| **SOC 2 audit trail** | ⬛⬛⬜ — Output logs only | ⬛⬛⬛ — Every tool call logged with agent ID, scope, and authorization decision | ⬛⬛⬛ — Per-agent isolated logs |

**Recommended approach**: **B (Per-Agent Scoped Access + MCP Security Gateway)**.

**Decision rationale**: Option A (shared access) creates the confused deputy problem — a prompt-injected orders agent could access the payment system. The OWASP MCP Top 10 identifies this as MCP02 (Privilege Escalation via Scope Creep). Option C (full isolation) eliminates cross-domain risk but at 50× infrastructure cost, which is infeasible. Option B implements the **Least-Agency** principle from the OWASP Agentic Top 10: each agent's MCP session is scoped to only its domain's tools via a per-agent access matrix. The MCP Security Gateway verifies tool schema hashes on every call (rug-pull detection, <5ms), enforces per-agent scope (<2ms), and logs every authorization decision to the WORM audit trail. JIT ephemeral tokens scoped to the specific operation prevent confused deputy attacks — the orders agent gets a token that works on orders.read and orders.write, but not on payment.charge. Total latency overhead: <100ms, well within the 200ms budget. At 10,000 concurrent sessions, the gateway handles scope checks at ~100K checks/second on a single instance (hash table lookup). The trusted tool registry with schema hash verification catches tool poisoning before any agent sees the compromised description.

---

*Module 13 complete. Covers OWASP LLM Top 10 (2025), prompt injection landscape (8 attack types with ASR data), 6 guardrail systems with benchmark scores, OWASP Agentic Top 10 and MCP Top 10, supply chain security (weight poisoning, AI-BOMs), 5-layer defense-in-depth architecture, compliance frameworks (EU AI Act, NIST AI RMF, ISO 42001, SOC 2), zero-trust for AI, and production security code.*
