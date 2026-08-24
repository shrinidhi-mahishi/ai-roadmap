# Topic 13: Security & Guardrails

## Overview

LLM security has evolved from a niche concern to a foundational requirement for production AI systems. The attack surface expanded dramatically with the rise of agentic AI, multimodal models, and protocols like MCP that connect LLMs to real-world tools and data. This topic covers the full spectrum: threat taxonomy, guardrail systems, red teaming, content safety, supply chain security, agent-specific threats, defense architecture, and compliance frameworks.

The landscape is characterized by an active arms race: a 2025 multi-model study synthesizing 128 peer-reviewed papers found that sophisticated multimodal attacks achieve over 90% success rates against unprotected systems, while advanced defense architectures demonstrate up to 95% protection ([Prompt Injection Survey, ScienceDirect](https://www.sciencedirect.com/org/science/article/pii/S1546221826001384)). No single defense is sufficient -- layered, defense-in-depth approaches are the industry consensus.

---

## 1. OWASP LLM Top 10 (2025 Edition, v2.0)

Published 18 November 2024, the 2025 edition substantially reworks the original list, adding two new categories and reordering based on community feedback ([OWASP Foundation](https://owasp.org/www-project-top-10-for-large-language-model-applications/), [OWASP GenAI Project](https://genai.owasp.org/llm-top-10/)).

### LLM01:2025 -- Prompt Injection
Remains the #1 vulnerability. LLMs process instructions and data in the same channel without clear separation, enabling attackers to craft input the model interprets as a new instruction. Includes **direct injection** (manipulating user prompts) and **indirect injection** (hidden instructions in external content -- documents, websites, emails the LLM processes). The fundamental challenge: traditional input validation is ineffective because the AI model itself is the exploited component ([OWASP LLM01](https://genai.owasp.org/llmrisk/llm01-prompt-injection/)).

### LLM02:2025 -- Sensitive Information Disclosure
Jumped from #6 to #2. LLMs can memorize and reproduce fragments of training data including PII, proprietary data, and confidential documents. Attackers use targeted queries to extract this data. Risk is amplified as LLMs are given broader access to organizational data for RAG and tool use ([Aembit Blog](https://aembit.io/blog/owasp-top-10-llm-risks-explained/)).

### LLM03:2025 -- Supply Chain
Compromised model weights, plugins, or training data undermine system integrity. Covers tampered components, malicious packages, and unverified third-party models ([OWASP GenAI](https://genai.owasp.org/llm-top-10/)).

### LLM04:2025 -- Data and Model Poisoning
Bad training data changes how the model behaves. Tampered training data can impair models, leading to responses that compromise security, accuracy, or ethical behavior. Research shows as few as 0.1% adversarially crafted training examples can reliably introduce backdoor behavior ([Oligo Security](https://www.oligo.security/academy/owasp-top-10-llm-updated-2025-examples-and-mitigation-strategies)).

### LLM05:2025 -- Improper Output Handling
Downstream systems trust model output without validation, leading to code execution, XSS, SSRF, and other exploits. The output becomes a vector for traditional injection attacks when passed to interpreters, databases, or APIs ([Gravitee Guide](https://www.gravitee.io/blog/owasp-top-10-for-llm-applications-2025-a-practical-guide)).

### LLM06:2025 -- Excessive Agency
Significantly expanded in 2025. Three root causes: **excessive functionality** (agents reach tools beyond scope), **excessive permissions** (tools operate with broader privileges than needed), **excessive autonomy** (high-impact actions proceed without human-in-the-loop). This is where most enterprise incidents originate ([SecureLayer7](https://securelayer7.net/learn/ai-security/owasp-llm-top-10)).

### LLM07:2025 -- System Prompt Leakage (NEW)
New category recognizing the risk of exposing system-level prompts that contain sensitive configuration details, access rules, or business logic. Attackers can extract these through targeted querying or indirect methods ([OWASP GenAI](https://genai.owasp.org/llm-top-10/)).

### LLM08:2025 -- Vector and Embedding Weaknesses (NEW)
Targets vulnerabilities in RAG systems and vector databases. Attackers can poison vector databases, exploit insufficient access controls across tenant boundaries, and manipulate embedding models to produce misleading similarity results ([OWASP GenAI](https://genai.owasp.org/llm-top-10/)).

### LLM09:2025 -- Misinformation
Renamed from "Overreliance." Focus sharpened: the risk is not just that users trust output too much, but that the model generates and propagates false information that other systems act upon. Particularly dangerous in automated pipelines where no human reviews the output ([Aembit Blog](https://aembit.io/blog/owasp-top-10-llm-risks-explained/)).

### LLM10:2025 -- Unbounded Consumption (EXPANDED)
Excessive or uncontrolled resource usage leading to DoS, financial exploitation, or unauthorized model replication. Expanded from the narrower "Model Denial of Service" to cover wallet-draining attacks in pay-per-use cloud environments ([OWASP GenAI](https://genai.owasp.org/llm-top-10/)).

### Key Changes from v1.1
- Three new categories added: System Prompt Leakage, Vector and Embedding Weaknesses, Unbounded Consumption
- "Insecure Plugin Design" and "Model Theft" no longer standalone -- absorbed into Supply Chain and Excessive Agency
- "Overreliance" renamed to "Misinformation" with sharpened focus

---

## 2. Prompt Injection & Jailbreaking

### 2.1 Direct vs. Indirect Prompt Injection

**Direct injection**: User manipulates their own input to override system instructions. Examples include "Ignore all previous instructions" patterns, role-playing exploits ("You are DAN"), and encoding tricks (Base64, ROT13, Unicode tags).

**Indirect injection**: Adversary embeds malicious instructions in external content (emails, web pages, documents) that the LLM retrieves during normal operation. Particularly dangerous because LLMs often cannot distinguish benign instructions from malicious instructions in untrusted data. In September 2024, researchers demonstrated ChatGPT memory exploitation, creating persistent "spAIware" that injected malicious instructions into long-term memory surviving across sessions ([Prompt Injection Survey, ScienceDirect](https://www.sciencedirect.com/org/science/article/pii/S1546221826001384)).

### 2.2 Universal Adversarial Suffixes (GCG Attack)

The July 2023 paper by Zou, Wang, Kolter, and Fredrikson introduced GCG (Greedy Coordinate Gradient) -- the first automated optimization procedure finding adversarial inputs via gradient descent, qualitatively different from manual jailbreaks ([JailbreakDB](https://www.jailbreakdb.com/posts/universal-adversarial-suffixes-gcg/)).

**Attack success rates (GCG-ensemble transfer):**
| Target Model | ASR (single suffix) | ASR (ensemble) |
|---|---|---|
| GPT-3.5 | 47.4% | 86.6% |
| GPT-4 | 29.1% | 46.9% |
| Claude-1 | 37.6% | 47.9% |
| Claude-2 | 1.8% | 2.1% |
| PaLM-2 | 36.1% | 66.0% |

**Key insight**: GCG suffixes have extremely high perplexity (statistically improbable token sequences). Defenses include perplexity filtering (catches GCG but may reject legitimate inputs), random token ablation, and dedicated GCG classifiers. Adaptive attackers can add perplexity constraints to generate lower-perplexity suffixes ([JailbreakDB](https://www.jailbreakdb.com/posts/universal-adversarial-suffixes-gcg/)).

### 2.3 Many-Shot Jailbreaking (MSJ)

Anthropic's research revealed how extended context windows create new vulnerabilities. Effectiveness follows power laws with the number of shots -- more shots yield higher attack success. MSJ is expected to be more effective on larger models unless resolved. MSJ can be combined with other jailbreaks (e.g., GCG) to yield successful attacks at shorter context lengths ([Anthropic MSJ Paper](https://www-cdn.anthropic.com/af5633c94ed2beb282f6a53c595eb437e8e7b630/Many_Shot_Jailbreaking__2024_04_02_0936.pdf)).

### 2.4 Crescendo Attack

Multi-turn technique that begins innocuously and gradually steers the model to generate harmful content in small, seemingly benign steps. Exploits the LLM's tendency to follow patterns and pay attention to its own recent output. Presented at USENIX Security 2025 by Russinovich, Salem, and Eldan (Microsoft) ([arXiv:2404.01833](https://arxiv.org/abs/2404.01833)).

**Performance**: Crescendomation achieves 29-61% higher ASR on GPT-4 and 49-71% higher on Gemini-Pro compared to other state-of-the-art techniques. Requires average of 9.4 trials per goal. Automated via Crescendomation tool. Once a multimodal model is jailbroken, it can be used for tasks across modalities (e.g., generating normally-refused images) ([USENIX Security 2025](https://www.usenix.org/conference/usenixsecurity25/presentation/russinovich)).

### 2.5 Skeleton Key Attack

Disclosed by Microsoft in June 2024. Unlike indirect approaches, Skeleton Key directly asks the model to augment its behavior guidelines to respond to any request with a warning disclaimer rather than an outright refusal. Tested against Meta Llama 3, Google Gemini Pro, GPT-3.5 Turbo, GPT-4o, Mistral Large, Claude 3 Opus, and Cohere Commander R Plus -- all models "complied fully and without censorship" ([Microsoft Security Blog](https://www.microsoft.com/en-us/security/blog/2024/06/26/mitigating-skeleton-key-a-new-type-of-generative-ai-jailbreak-technique/)).

### 2.6 Multimodal Prompt Injection

Emerging area (2025-26) with few deployed defenses:
- **Image-based injection**: Rendering adversarial text inside an image allows vision-language models to OCR/encode it as instruction; no text-channel filter sees it
- **Cross-modal attacks**: Using one modality to attack behavior in another (e.g., image hidden text triggers a tool call that exfiltrates data via markdown image)
- **Claude Unicode tag exploitation**: Hidden Unicode tag instructions passing through UI and API layers
- **Audio/video injection**: Embedding instructions in audio transcripts or video frames ([ZioSec Guide](https://ziosec.com/blog/ai-jailbreak-techniques-in-2026-a-complete-technical-guide-ziosec))

### 2.7 Automated Jailbreaking Methods

| Method | Description | ASR | Trials Required |
|---|---|---|---|
| TAP (Tree of Attacks with Pruning) | Tree-search strategy with pruning | 96% | ~25.4 per goal |
| PAIR (Prompt Automatic Iterative Refinement) | Iterative refinement using attacker LLM | High | Minimal queries |
| GAP (Graph of Attacks with Pruning) | Graph-based pruning variant | High | ~8.6 per goal |
| Crescendomation | Automated multi-turn escalation | High | ~9.4 per goal |
| AutoDAN | Automated DAN generation | High | Varies |
| GPTFuzzer | Fuzzing-based jailbreak generation | High | Varies |

An October 2025 study involving researchers from OpenAI, Anthropic, and Google DeepMind examined 12 published defenses and found adaptive attacks could bypass most with success rates above 90% ([Prompt Injection Survey](https://www.sciencedirect.com/org/science/article/pii/S1546221826001384)).

---

## 3. Guardrail Systems

### 3.1 Taxonomy of Guardrail Approaches

Guardian models are typically small (2B-8B parameters) but reliably catch harmful content across dozens of risk categories. No single model is sufficient -- the recommended pattern is two models with non-overlapping strengths, ensembled with ANY-logic for high-stakes categories, single-model for routine traffic. Latency budgets: sub-100ms p50 for input rails, sub-150ms p50 for output rails ([Digital Applied](https://www.digitalapplied.com/blog/llm-guardrails-production-safety-layers-reference-2026), [FutureAGI Guide](https://futureagi.com/blog/ultimate-guide-llm-guardrails-2026/)).

### 3.2 Llama Guard 4

Released April 2025 by Meta. 12B parameter natively multimodal model (text + image moderation). Pruned from Llama 4 Scout MoE model to a dense shared-expert network. Aligned to MLCommons hazards taxonomy. Distributed on Hugging Face as `Llama-Guard-4-12B`. Limitations: may lack current knowledge for Defamation (S5), Intellectual Property (S8), and Elections (S13). Important caveat: because Llama Guard is itself a language model, it is susceptible to prompt injection, reinforcing that no single guardrail layer is sufficient ([TuringPost](https://www.turingpost.com/p/guardianmodels), [TechJack Solutions](https://techjacksolutions.com/ai-tools/meta-llama/mastering-llama-safety-and-guardrails/)).

**Meta's defense stack**: PromptGuard (input-side injection detection) + Llama Guard 4 (content classification on input and output) + CodeShield (code safety scanning).

### 3.3 ShieldGemma 2

Google's updated image moderation model covering sexually explicit content, violence/gore, and dangerous content. The 2B variant is the latency-optimized option, usable as a pre-filter with larger models only processing flagged chunks ([Haystack Cookbook](https://haystack.deepset.ai/cookbook/safety_moderation_open_lms)).

### 3.4 Granite Guardian (IBM)

Suite of safety classifiers available in 2B, 3B, 5B, and 8B sizes. Covers social bias, profanity, violence, sexual content, unethical behavior, jailbreaking, AND hallucination detection (context relevance, groundedness, answer accuracy in RAG). Published at NAACL 2025. Latest version (4.1) is a hybrid thinking model with optional reasoning traces. Apache 2.0 license ([IBM Research](https://research.ibm.com/blog/granite-guardian-tops-guardbench), [ACL Anthology](https://aclanthology.org/2025.naacl-industry.49/)).

**Benchmark performance**: Holds 6 of top 10 spots on GuardBench leaderboard. Top scores: 86% and 85% across 40 datasets, vs. NVIDIA (82%, 80%) and Meta (78%, 76%). IBM Fellow Kush Varshney: "There is no other single guard model that is so comprehensive across risks and harms" ([IBM Research](https://research.ibm.com/blog/granite-guardian-tops-guardbench)).

### 3.5 Anthropic's Constitutional Classifiers

Dual-layer architecture: input classifiers and output classifiers trained on synthetic data generated from a constitution defining categories of harmful and harmless content. Natural language rules enable rapid adaptation to new threats through constitution updates ([Anthropic Research](https://www.anthropic.com/research/constitutional-classifiers)).

**Results**:
- First generation: reduced jailbreak success rate from 86% to 4.4%, blocking 95% of attacks that bypass built-in safety training
- Survived thousands of hours of human red teaming for universal jailbreaks
- HackerOne challenge (Feb 2025): 339 participants, 300,000+ interactions, ~3,700 collective hours. $55,000 in payouts across four winning teams. One confirmed universal jailbreak found

**Next-generation (Constitutional Classifiers++)**: Two-stage ensemble -- a probe examining Claude's internal activations (very cheap) screens all traffic, escalating suspicious exchanges to a more powerful classifier. Lowest successful attack rate of any approach Anthropic has tested. No universal jailbreak discovered as of announcement ([Anthropic Research](https://www.anthropic.com/research/next-generation-constitutional-classifiers)).

**ASL-3 Integration**: Part of Anthropic's AI Safety Level 3 deployment under the Responsible Scaling Policy. Three-part defense: (1) classifier intervention, (2) detection via monitoring and bug bounty, (3) iterative improvement via synthetic jailbreak generation ([Anthropic ASL-3](https://www.anthropic.com/asl3-deployment-safeguards)).

### 3.6 NVIDIA NeMo Guardrails

Open-source Python package for adding programmable guardrails to LLM applications. Sits between application and LLM as a runtime safety layer. Policies written in **Colang** (domain-specific language for dialog flows). Unlike model-level alignment, NeMo Guardrails is enforced at request time regardless of model training ([NVIDIA Docs](https://docs.nvidia.com/nemo/guardrails/about-nemo-guardrails-library/overview), [GitHub](https://github.com/NVIDIA-NeMo/Guardrails)).

**Five rail types**:
1. **Input rails** -- jailbreak detection, prompt injection filtering, content moderation, intent classification
2. **Dialog rails** -- conversation flow control using Colang, keeping LLM on topic across multiple turns (unique differentiator)
3. **Retrieval rails** -- filter/validate knowledge base results before reaching LLM, reducing hallucination
4. **Execution rails** -- gate tool/action calls, validate tool inputs/outputs before and after invocation
5. **Output rails** -- fact-checking, hallucination detection, sensitive data blocking, response quality validation

**Deployment**: Library (Python dev) + Microservice (production container). Configurations portable between both. 2026 engine optimized with 40% reduced baseline overhead vs. 2025. Integration with LangGraph for multi-agent safety ([NVIDIA NeMo Docs](https://docs.nvidia.com/nemo/guardrails/latest/index.html)).

**Enterprise integration**: F5 AI Guardrails + NeMo Guardrails provides centralized security and governance, separating security enforcement from AI framework ([TheFastMode](https://www.thefastmode.com/technology-solutions/49973-f5-integrates-ai-guardrails-with-nvidia-nemo-guardrails-to-strengthen-enterprise-ai-security)).

### 3.7 Guardrails AI

Open-source programmatic framework for LLM output validation using Python or JavaScript. Core concepts: **Guard** (main validation interface) and **Validators** (test output against specific conditions). Validators Hub provides dozens of pre-built validators for toxicity, PII, format compliance, etc. ([GuardrailsAI Blog](https://guardrailsai.com/blog/nemoguardrails-integration)).

**Complementarity with NeMo Guardrails**: NeMo for conversation management + Guardrails AI for output validation. NeMo Guardrails can access GuardrailsAI PII validators alongside other integrations (Presidio, Private AI, Polygraf) ([is4.ai Comparison](https://is4.ai/blog/our-blog-1/guardrails-ai-vs-nemo-guardrails-comparison-2026-352)).

**When to choose**: Guardrails AI for structured output validation, code generation, API-style apps. NeMo Guardrails for conversational AI, dialog flow control, sophisticated jailbreak prevention ([is4.ai Comparison](https://is4.ai/blog/our-blog-1/guardrails-ai-vs-nemo-guardrails-comparison-2026-352)).

### 3.8 Other Guardrail Systems

| System | Type | Key Features |
|---|---|---|
| **Lakera Guard** | Commercial API | Prompt injection detection, PII redaction, toxicity. v2 integration via LiteLLM |
| **WildGuard** | Open-source classifier | One-stop moderation for safety risks, jailbreaks, and refusals |
| **Azure AI Content Safety** | Cloud service | Microsoft's content moderation API |
| **Qwen3-Guard** | Open-source classifier | Strong performance on non-English languages (Hindi, Vietnamese) |
| **Detoxify** | Open-source | Toxicity scoring using Perspective API methodology |
| **LLM Guard** | Open-source | Input/output scanning, PII detection, prompt injection detection |

---

## 4. Red Teaming

### 4.1 NIST AI 600-1 (Generative AI Profile)

Published July 2024 as the Generative AI Profile of the AI Risk Management Framework. Strongly recommends red teaming before and after deployment across 12 GenAI risk categories including misinformation generation, cybersecurity attacks, private information leakage, and emotional manipulation ([NIST AI 600-1 PDF](https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf)).

**NIST AI 100-2 E2025** (March 2025 update): Authoritative classification framework for attack categories relevant to AI agent deployments. Key finding: when red teamers developed novel attack techniques tailored to specific behavioral patterns of LLM-backed agents, task-hijacking success rates rose from 11% to 81%. NIST recommends pairing benchmark scores with novel, target-specific attacks -- relying entirely on existing tooling gives a false sense of assurance ([CSA Lab Space](https://labs.cloudsecurityalliance.org/research/csa-research-note-nist-ai-agent-red-teaming-standards-202603/)).

### 4.2 Automated Red Teaming Tools

**Promptfoo** (Open Source, MIT License):
- CLI and library for evaluating and red-teaming LLM apps. 22,351 GitHub stars, 255 contributors
- Covers 50+ vulnerability types: prompt injection, jailbreaks, PII leaks, tool misuse, toxic content
- CI/CD integration -- red teaming runs on every PR
- Used by OpenAI and Anthropic. 350,000+ developers, 25%+ of Fortune 500
- Acquired by OpenAI March 2026 for undisclosed terms (valued at $86M at July 2025 Series A)
- Install: `npm install -g promptfoo` or `brew install promptfoo`
([Promptfoo GitHub](https://github.com/promptfoo/promptfoo), [Promptfoo Docs](https://www.promptfoo.dev/docs/red-team/))

**HarmBench**:
- Standardized framework with 500+ curated harmful behaviors across semantic categories
- Includes attack methods and evaluation classifiers for reproducible comparisons
- Integrates methods: AutoDAN, PAIR, TAP, and more
- Best for academic and research-oriented safety evaluation
([HarmBench GitHub](https://github.com/centerforaisafety/HarmBench))

**Other Tools**:
| Tool | Focus | Key Feature |
|---|---|---|
| PAIR | Iterative jailbreak refinement | Attacker LLM generates refined attacks |
| TAP | Tree-search jailbreaking | 96% ASR with pruning |
| Mindgard | Enterprise AI red teaming | Continuous automated testing |
| Gray Swan AI | Red team arena | Challenge-based adversarial testing |
| DeepTeam | LLM red teaming framework | OWASP-aligned vulnerability testing |

### 4.3 Best Practices

**Hybrid approach (recommended)**: Human experts design attack strategies and edge cases, automated tools execute variations at scale, human evaluators assess results. NIST AI 100-2 specifically recommends combining automated and manual testing ([Mindgard Guide](https://mindgard.ai/blog/what-is-ai-red-teaming)).

**Continuous red teaming**: Not a one-time event. Integrated into CI/CD pipeline. Monitor for new attack techniques as they emerge. Update test suites when new jailbreaks are published. Red teaming should cover both model-layer vulnerabilities (injection, jailbreaks, bias, hallucination, PII extraction) and application-layer vulnerabilities (indirect injection, RAG leaks, tool exploits, data exfiltration) ([NVISO Blog](https://blog.nviso.eu/2026/02/05/an-introduction-to-automated-llm-red-teaming/)).

---

## 5. Agent-Specific Threats

### 5.1 OWASP Top 10 for Agentic Applications (2026)

Published December 2025, this is a dedicated framework for autonomous AI agent security risks (ASI01 through ASI10) ([OWASP GenAI](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/)).

Key entries most relevant to agent security:

**ASI05 -- Unexpected Code Execution & Sandbox Escape**: Agent-generated or externally influenced code executes in unintended ways, leading to host compromise, persistence, or sandbox escape. It is no longer just a single manipulated output -- it is orchestrated multi-tool chains where a sequence of otherwise legitimate calls leads to code execution with real system impact. Canonical example: AutoGPT RCE research. Mitigation: ephemeral, network-isolated micro-VMs (Firecracker) or WebAssembly (Wasm) sandboxes with all privileges dropped ([Cycode](https://cycode.com/blog/owasp-top-10-agentic-applications/)).

**Excessive Agency (core principle)**: Agents granted broad permissions exceeding actual task requirements create the largest attack surface. ASI Top 10 introduces the principle of **Least-Agency** -- agents should only be granted minimum autonomy required for their defined task. Mitigations: JIT ephemeral tokens, HITL controls, strictly sandboxed environments with explicit guardrails ([Authensor](https://www.authensor.com/updates/owasp-agentic-top-10-explained)).

### 5.2 OWASP MCP Top 10 (2025)

First OWASP framework dedicated to Model Context Protocol security, currently in beta. Catalogs the ten most common risk categories in MCP deployments ([OWASP Foundation](https://owasp.org/www-project-mcp-top-10/)):

| ID | Category | Description |
|---|---|---|
| MCP01 | Token Mismanagement & Secret Exposure | Hard-coded credentials, long-lived tokens, secrets in model memory |
| MCP02 | Privilege Escalation via Scope Creep | Loosely defined permissions expand over time |
| MCP03 | Tool Poisoning | Compromised tools/plugins inject malicious context. Sub-techniques: rug pulls, schema poisoning, tool shadowing |
| MCP04 | Software Supply Chain Attacks | Compromised MCP packages, typosquatted servers. First malicious MCP package: September 2025 |
| MCP05 | Command Injection & Execution | Agents build shell commands/SQL from untrusted input |
| MCP06 | Prompt Injection via Contextual Payloads | Hidden instructions in data loaded into MCP context |
| MCP07 | Insufficient Authentication & Authorization | Weak/missing auth on MCP endpoints |
| MCP08 | Lack of Audit and Telemetry | Missing logging makes incidents undetectable |
| MCP09 | Shadow MCP Servers | Unapproved MCP instances outside security governance |
| MCP10 | Context Injection & Over-Sharing | Too much sensitive context passed into agent's window |

**Critical vulnerability**: CVE-2025-6514 in mcp-remote scored CVSS 9.6, affecting 437,000+ downloads before disclosure. Researchers filed 30+ CVEs in 60 days in early 2026 ([PipeLab](https://pipelab.org/learn/owasp-mcp-top10/), [Practical DevSecOps](https://www.practical-devsecops.com/owasp-mcp-top-10/)).

### 5.3 Tool Poisoning Deep Dive

Invariant Labs demonstrated a scenario with WhatsApp MCP server: a poisoned tool description on a secondary server instructed the LLM to exfiltrate the user's entire WhatsApp message history through a seemingly benign tool invocation, with no visible indication of data exfiltration. Tool descriptions and schemas are placed directly into the agent's context window as trusted instructions, making them ideal injection vectors ([arXiv:2603.22489](https://arxiv.org/html/2603.22489v1)).

Traditional SAST and SCA tools cannot detect tool poisoning because malicious content lives in metadata fields the scanner has no reason to read. The visibility gap compounds with every new MCP server added ([Cycode](https://cycode.com/blog/owasp-mcp-top-10/)).

### 5.4 Confused Deputy Problem

The MCP server executes actions with its own (often broad) privileges, not the requesting user's permissions. An agent's tools become a confused deputy when prompt injection causes the agent to use a tool on behalf of an attacker. Mitigations: validate on each request that the session/token belongs to the current requester, least-privilege tool design, per-action authorization, scoped tokens, HITL for high-impact actions ([OWASP MCP Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/MCP_Security_Cheat_Sheet.html)).

### 5.5 Real-World Agent Security Incidents (2025-2026)

- **Check Point disclosed CVE-2025-59536 (CVSS 8.7) and CVE-2026-21852 (CVSS 5.3)** in Claude Code: repository-level configuration files function as part of the execution layer -- cloning an untrusted project can trigger RCE and API key exfiltration before any user consent dialog
- **ClawHub registry poisoning**: Five of top seven most-downloaded OpenClaw skills confirmed as malware -- first AI agent registry systematically poisoned at scale (Q1 2026)
- **Cursor IDE vulnerabilities (CVE-2025-54135, CVE-2025-54136)**: Prompt injection into developer tools leading to remote code execution and system compromise
([Cycode](https://cycode.com/blog/owasp-top-10-agentic-applications/), [OWASP Agentic Top 10](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/))

### 5.6 CSA Agentic MCP Security Maturity Model

The Cloud Security Alliance recommends a layered maturity approach ([CSA Labs](https://labs.cloudsecurityalliance.org/agentic/agentic-mcp-security-best-practices-v1/)):
- **Level 1**: Eliminate critical baseline risks -- unauthenticated servers, unencrypted communications
- **Level 2**: Address tool integrity and session management risks enabling tool poisoning and rug pulls
- **Level 3**: Supply chain governance and behavioral monitoring for production enterprise deployments
- **Level 4**: Zero-trust across the full tool invocation lifecycle

---

## 6. Content Safety

### 6.1 PII Detection & Redaction

**Microsoft Presidio** (Open Source):
- Python framework detecting and anonymizing sensitive data
- Two components: analyzer (identifies PII position/type) and anonymizer (replaces with non-identifiable information)
- Built on spaCy 3+ NER, supports Stanza, transformers, and Flair via custom recognizers
- Requires substantial build work before satisfying HIPAA/GDPR audit
([Ploomber Blog](https://ploomber.io/blog/presidio/))

**Standard PII redaction pattern**: Detect entities -> Replace with typed placeholders (e.g., `[PERSON]`, `[EMAIL]`) -> Send sanitized text to LLM -> Optionally re-map placeholders in response. Keep encrypted mapping outside model path. Scan response. Re-identify only authorized placeholders at final delivery boundary ([Wavect Blog](https://wavect.io/blog/pii-redaction-before-llm-prompts/), [Gravitee Blog](https://www.gravitee.io/blog/how-to-prevent-pii-leaks-in-ai-systems-automated-data-redaction-for-llm-prompt)).

**GDPR context**: EDPB 2025 guidance is explicit that pseudonymized data remains personal data when it can be linked back to a person (Article 4(5)). OWASP elevated Sensitive Information Disclosure to LLM02 in 2025, noting LLMs now require broader access to organizational data, dramatically widening the exposure surface ([Prediction Guard Blog](https://predictionguard.com/blog/pii-detection-redaction-llm-pipelines-regulated-industries)).

**LiteLLM integration**: Supports Presidio, Lakera, Guardrails AI, Bedrock, and other providers as pluggable guardrails with modes: pre_call, post_call, during_call, logging_only ([LiteLLM Docs](https://docs.litellm.ai/docs/proxy/guardrails/quick_start)).

### 6.2 Toxicity Detection

Key tools and approaches:
- **RealToxicityPrompts** (Gehman et al., 2020): Foundational benchmark for evaluating toxicity in LLM outputs
- **Perspective API**: Google's toxicity scoring service
- **Detoxify**: Open-source toxicity classification
- **Commercial APIs**: Black-box services for multi-language toxicity detection
- **Enterprise platforms**: Continuous scanning integrated with model inference pipelines using statistical and rule-based anomaly detection with production dashboards ([Deepchecks](https://deepchecks.com/llm-hallucination-detection-and-mitigation-best-techniques/))

### 6.3 Bias Mitigation

- LLMs reflect biases in training data. Evaluation methods include using additional models to score text, analyzing LLM continuations for gender/occupational biases
- Ensemble methods combining outputs from multiple LLMs increase robustness and mitigate individual model biases
- Constitutional AI approaches encode fairness principles directly into the model's training process
- Post-hoc debiasing: applying corrections to model outputs based on detected biases
([Springer Nature](https://link.springer.com/article/10.1007/s10462-024-10896-y))

### 6.4 Hallucination Detection & Mitigation

**Root causes**: Noisy training data, architectural quirks, decoding randomness, plus the systemic issue that training objectives and benchmarks reward confident guessing over calibrated uncertainty ([arXiv Hallucination Survey](https://arxiv.org/html/2510.06265v2)).

**Detection approaches**:
- **Semantic entropy**: Measures agreement across different model outputs. Higher consistency correlates with factual accuracy
- **Expected Calibration Error (ECE)**: Quantifies miscalibration -- the disconnect between confidence scores and actual accuracy. Flags "confidently wrong" scenarios
- **Self-consistency checking**: Integrative Decoding leverages agreement across outputs to enhance factuality
- **NLI-based scoring**: Natural language inference for fact verification
- **LLM-as-a-judge**: Using auxiliary models to evaluate factuality
([Zylos Research](https://zylos.ai/zh/research/2026-01-27-llm-hallucination-detection-mitigation/))

**Mitigation effectiveness**: A 2025 study in *npj Digital Medicine* showed prompt-based mitigation cut GPT-4o hallucination rate from 53% to 23%. Modern multi-layered approaches reduce hallucination rates by up to 96% in production systems. Complete elimination remains impossible as it is tied to LLM creative capabilities ([Lakera Blog](https://www.lakera.ai/blog/guide-to-hallucinations-in-large-language-models)).

**Taxonomy**: Over 300 studies organized into six categories: Training and Learning Approaches, Architectural Modifications, Input/Prompt Optimization, Post-Generation Quality Control, Interpretability and Diagnostic Methods, and Agent-Based Orchestration ([MDPI Survey](https://www.mdpi.com/2673-2688/6/10/260)).

---

## 7. Defense Architecture

### 7.1 Defense-in-Depth for LLMs

The core tenet: no single security control is infallible. Multiple independent defensive measures operate at different levels of the inference stack. If one layer is bypassed, subsequent layers detect, prevent, or mitigate the attack's progression ([IOSEC.IN](https://iosec.in/end-to-end-llm-security-architecture/)).

**Reference architecture (5 layers)**:

1. **Edge Protection (API Gateway)**: Authentication, basic rate limiting, TLS termination, malformed request filtering, excessively large payload rejection. Must also route requests through AI-specific middleware before inference endpoints ([Secure By Dezign](https://www.securebydezign.com/articles/securing-ai-apis-beyond-rate-limiting.html)).

2. **Semantic Firewall**: Dedicated service performing prompt injection detection, topic boundary enforcement, PII scanning. Maintains its own ML models and rule sets, updated independently of production LLM ([Introl Blog](https://introl.com/blog/llm-security-prompt-injection-defense-production-guide-2025)).

3. **Context Management**: Stateful service managing conversation history, context windowing, system prompt isolation ([OWASP Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html)).

4. **Inference Proxy**: Circuit breakers, model failover, observability into inference behavior. Detects model-level anomalies. Tracks violation rates over sliding time windows (e.g., 5+ violations in 60s) and triggers circuit breaker actions: revoking API keys, switching to safe-mode models, alerting SOC ([IOSEC.IN](https://iosec.in/end-to-end-llm-security-architecture/)).

5. **Output Pipeline**: Content filtering, data leakage detection, response sanitization. Input gets deeper checks (attacker has agency there), output gets faster and stricter checks (harm leaves the system there) ([APXML](https://apxml.com/courses/llm-alignment-safety/chapter-5-adversarial-attacks-defenses-llms/input-output-filtering-defenses)).

### 7.2 Input Sanitization Techniques

- Prompt injection detection (identifying override attempts)
- Input sanitization (removing/escaping dangerous patterns)
- Topic classification (rejecting off-topic queries)
- PII detection (redacting personal information)
- Blocking known attack patterns (jailbreak signatures)
- Neutralizing meta-instructions ("Ignore all previous instructions")
- Input canonicalization (neutralizing encoding-based bypasses -- Base64, ROT13, Unicode)
([OWASP LLM Prompt Injection Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html))

### 7.3 Output Filtering

- Toxicity detection (Detoxify, Perspective API)
- Factuality checking (comparing against retrieval sources)
- PII scanning (detecting leaked sensitive data)
- Code safety scanning (blocking dangerous code patterns)
- Format validation (ensuring structured output compliance)
([APXML](https://apxml.com/courses/llm-alignment-safety/chapter-5-adversarial-attacks-defenses-llms/input-output-filtering-defenses))

### 7.4 Rate Limiting & Circuit Breakers

**Rate limiting**: Necessary but grossly insufficient for AI APIs. A single well-crafted prompt can achieve what thousands of malformed requests cannot. Rate limiting only increases computational cost for attackers. Must be combined with semantic-level defenses ([Secure By Dezign](https://www.securebydezign.com/articles/securing-ai-apis-beyond-rate-limiting.html)).

**Circuit breakers (model-level)**: Representation Rerouting (RR) uses representation engineering to create "circuit breakers" that trigger refusal behavior when attack-related representations are detected. Achieves 85-90% ASR reduction but requires careful hyperparameter tuning ([arXiv:2406.04313](https://arxiv.org/pdf/2406.04313)).

**Circuit breakers (operational)**: Guard models feed into operational safeguards that automatically halt inference when violation thresholds are breached. Track violation rates over sliding windows, trigger actions: revoke API keys, switch to safe-mode models, alert SOC ([InDepth.dev](https://indepth.dev/posts/2013/en/building-model-armor-multi-layer-llm-safety-filtering)).

### 7.5 TRYLOCK: Defense-in-Depth Research

First defense-in-depth architecture combining four heterogeneous mechanisms (arXiv:2601.03300, January 2026):
1. **Layer 0**: Input canonicalization (neutralizes encoding bypasses)
2. **Layer 1**: Weight-level safety alignment via DPO (zero latency -- LoRA merged into weights)
3. **Layer 2**: Activation-level control via RepE steering (10% overhead)
4. **Layer 3**: Adaptive steering strength via sidecar classifier (SAFE/WARN/ATTACK)

**Results on Mistral-7B**: 88.0% relative ASR reduction (46.5% to 5.6%). Each layer contributes unique coverage: RepE blocks 36% of attacks bypassing DPO; canonicalization catches 14% of encoding attacks evading both. Adaptive sidecar reduces over-refusal from 60% to 48% while maintaining identical attack defense ([arXiv:2601.03300](https://arxiv.org/abs/2601.03300)).

### 7.6 Model Armor Pattern

"Model Armor is not one technique but an engineering pattern. Fast, cheap filters handle the bulk of cases. Expensive reasoning handles the edge cases. Every layer has a fallback." The asymmetry is intentional: input gets deeper checks, output gets faster and stricter checks ([InDepth.dev](https://indepth.dev/posts/2013/en/building-model-armor-multi-layer-llm-safety-filtering)).

### 7.7 Key Design Principles

1. **Treat the LLM as a hostile user**: Put agentic functions behind the same API gateways, rate limiters, and IAM boundaries used for external traffic
2. **A guardrail LLM is itself susceptible to prompt injection**: Treat it as one layer, not a replacement for input validation, structured prompts, least-privilege tool scopes, or human approval on destructive actions
3. **No foolproof prevention exists**: Only risk reduction through layered defenses. Organizations must accept this and build security programs accordingly
4. **Audit logging, distributed tracing, circuit breakers, and red teaming don't have a "done" state**: They mature with the system
([AI Safety Directory](https://aisecurityandsafety.org/en/guides/llm-guardrails/))

---

## 8. Supply Chain Security

### 8.1 The AI Supply Chain Attack Surface

A single LLM deployment may pull in a base model from Hugging Face, a tokenizer from a different repo, a vector database, a RAG document store, and a dozen Python packages -- each with its own upstream maintainers and opportunity for compromise. The AI supply chain has unique risks: a training corpus that can be silently poisoned, model file formats (pickle) that execute arbitrary code on deserialization, and fine-tuning steps that can reintroduce backdoors even after auditing ([SafeGuard.sh](https://safeguard.sh/resources/blog/overview-of-ai-model-supply-chain-security-risks-end-to-end), [GLACIS Guide](https://www.glacis.io/guide-ai-supply-chain-security)).

### 8.2 Weight/Model Poisoning

**October 2025 landmark study** (Anthropic, UK AI Security Institute, Alan Turing Institute): The number of malicious documents needed to plant a backdoor is near-constant (~250) regardless of model size. A 13B model trained on 20x more data than a 600M model was backdoored by the same small count. This overturned the assumption that larger models are harder to poison ([Polygraf AI](https://www.polygraf.ai/blogposts/ai-supply-chain-security-stop-model-poisoning/)).

**Sleeper agents**: Models can be trained to write secure code normally but insert exploitable vulnerabilities when detecting a deployment year trigger. Standard safety fine-tuning (RLHF, constitutional AI) does not remove the behavior -- it only suppresses the trigger's surface-level manifestation ([CISO Marketplace](https://cisomarketplace.com/blog/ai-model-supply-chain-training-data-poisoning-open-source-risk)).

**The verification gap**: You cannot fully verify a model you didn't train. Hash verification only proves you got the publisher's intended model, not that the publisher's model is clean. No widely adopted mechanism exists for cryptographically signing model weights with verification at load time, no reproducible training, no CVE equivalent for model vulnerabilities ([TechBytes](https://techbytes.app/posts/supply-chain-poisoning-in-ai-models-deep-dive-2026/)).

### 8.3 Real-World Supply Chain Incidents

- **Hugging Face malicious models (2024)**: JFrog identified ~100 malicious models with code execution payloads using pickle deserialization tricks
- **NullifAI**: Poisoned PyTorch models on Hugging Face evading Picklescan check
- **Ultralytics compromise (December 2024)**: Attackers exploited GitHub Actions cache-poisoning to inject malicious code into automated builds. Package published to PyPI differed from reviewed source, running a cryptocurrency miner. Propagated through countless downstream products
- **First malicious MCP package**: September 2025
([Infosec.qa](https://infosec.qa/blog/ai-supply-chain-attacks/), [Pharos Production](https://pharosproduction.com/insights/engineering/ai-supply-chain-security-2026/))

### 8.4 AI-BOMs (AI Bill of Materials)

Extends the SBOM concept to AI models, documenting: model provenance, training data manifests, dependency chains, evaluation results, known limitations, modification history. SBOMs no longer provide a complete inventory for AI-infused supply chains ([The Register](https://www.theregister.com/2026/05/04/ai_bom_supply_chain/)).

**Recommended format**: CycloneDX ML-BOM -- extends widely-adopted SBOM standard with growing tool support. Most organizations in 2026 are still at maturity Level 1 or 2, below the Level 3 baseline recommended for systems handling sensitive data ([Pharos Production](https://pharosproduction.com/insights/engineering/ai-supply-chain-security-2026/)).

### 8.5 Model Provenance Tools

**Cisco Model Provenance Kit** (open source): "A DNA test for AI models." Compare or scan modes. Fingerprint database covering ~150 base models across 45+ families and 20+ publishers. Emerging tools generate ML-BOMs automatically during CI/CD with cryptographic hashes and provenance chains ([GLACIS Guide](https://www.glacis.io/guide-ai-supply-chain-security)).

### 8.6 Defensive Measures

- **Safe serialization**: Avoid pickle. Use safetensors or ONNX. Sandbox model loading
- **Dependency pinning**: Lock specific versions of all packages
- **Model signing**: Cryptographic attestation at each supply chain step
- **Staged promotion**: Separate evaluation environments before production
- **Behavioral verification**: Test model behavior against use-case-specific criteria
- **Sandboxed evaluation**: Run untrusted models in isolated environments
([Polygraf AI](https://www.polygraf.ai/blogposts/ai-supply-chain-security-stop-model-poisoning/), [GLACIS Guide](https://www.glacis.io/guide-ai-supply-chain-security))

---

## 9. Compliance Frameworks

### 9.1 EU AI Act

The first comprehensive AI regulation, entered into force August 2024, with phased enforcement through 2027 ([Orbit Reconn](https://orbit.reconn.io/eu-ai-act/), [EC Council](https://www.eccouncil.org/cybersecurity-exchange/responsible-ai-governance/eu-ai-act-nist-ai-rmf-and-iso-iec-42001-a-plain-english-comparison/)).

**Risk Tiers**:

| Tier | Description | Requirements | Examples |
|---|---|---|---|
| **Unacceptable** (Banned) | AI systems enabling manipulation, exploitation, social control | Prohibited outright | Social scoring, subliminal manipulation, untargeted facial recognition scraping, emotion recognition in workplaces/schools |
| **High Risk** | AI in critical domains | Adequate risk assessment, high-quality datasets, activity logging, documentation, human oversight, high resilience | Critical infrastructure, education, employment, essential services, law enforcement, justice |
| **Limited Risk** | Systems requiring transparency | Transparency obligations | Chatbots (must disclose AI nature), deepfakes |
| **Minimal Risk** | Low-risk applications | No specific restrictions | Spam filters, AI-enabled video games |

**Key Timeline**:
- February 2025: Prohibited practices banned. AI literacy obligations begin
- August 2025: Governance infrastructure operational. GPAI model obligations begin
- August 2026: High-risk system requirements fully applicable (updated: Annex III obligations now December 2027 per Omnibus VII of 7 May 2026)

**Penalties**: Up to EUR 35M / 7% global turnover for prohibited AI; EUR 15M / 3% for high-risk violations; EUR 7.5M / 1.5% for providing incorrect information.

### 9.2 NIST AI Risk Management Framework (AI RMF)

Voluntary framework developed in consultation with industry and civil society. Does not carry force of law, but widely referenced by regulators. Defines risk as likelihood and magnitude of harm. Four core functions: **Govern, Map, Measure, Manage** ([Compyl](https://compyl.com/blog/ai-governance-frameworks-compared-nist-iso-42001-eu-ai-act/)).

**Regulatory influence**: Referenced by FTC, CFPB, FDA, SEC, EEOC, and Department of Defense. Federal procurement increasingly expects NIST alignment. Enterprise customers use it as the benchmark for evaluating vendor AI governance maturity ([GAICC](https://gaicc.org/blog/ai-governance-comparison-eu-ai-act-nist-iso-42001/)).

**NIST AI 600-1** (July 2024): Generative AI Profile extending RMF to GenAI-specific risks. 12 risk categories. Explicitly calls for approved third-party provider lists, provenance records, and incident response plans.

### 9.3 ISO/IEC 42001

International standard for AI Management Systems (AIMS). Voluntary but certifiable. Applicable across all industries and organization sizes. Covers 38 controls in 9 objectives ([Trustible](https://trustible.ai/post/ai-governance-frameworks-nist-ai-rmf-eu-ai-act-iso-42001-compared/)).

**Certification**: Three-year cycle with annual surveillance audits. Stage 1 readiness audit (remote) + Stage 2 detailed on-site audit covering all 38 controls.

**Maps to NIST AI RMF**: Direct alignment with all four functions (Govern, Map, Measure, Manage). Published crosswalks exist. Covers significant portion of EU AI Act high-risk system requirements, but the Act includes legal obligations (conformity assessments, EU database registration, GPAI rules) beyond any voluntary standard ([CSA Blog](https://cloudsecurityalliance.org/blog/2025/01/29/how-can-iso-iec-42001-nist-ai-rmf-help-comply-with-the-eu-ai-act)).

### 9.4 SOC 2 for AI Systems

The AICPA Trust Services Criteria (TSC) applies to AI companies the same way it applies to any cloud/software provider handling customer data, but auditors in 2026 now examine AI-specific controls ([Baker Tilly](https://www.bakertilly.com/insights/ai-controls-for-soc-2-reports), [SOC2 Auditors](https://soc2auditors.org/insights/soc-2-for-ai-companies/)).

**AI-specific control areas auditors test (2026)**:
1. **Model versioning**: Clear, auditable history of every deployed model version (Git-LFS, DVC)
2. **Drift monitoring**: Automated tracking of performance metrics against baseline with alerts
3. **Training data governance**: CyLab 2025 found 0.1% adversarially crafted examples sufficient for effective poisoning
4. **Bias and fairness testing**: Appeared as non-conformity in 38% of 2025 audits
5. **Explainability**: Whether model outputs are traceable and auditable
6. **Access controls**: Who can modify or retrain models
7. **Agent governance**: For AI agents -- "what authorized these actions?"
8. **Runtime behavior controls**: "Show me the control that governs runtime behavior and the evidence it worked"

**Common audit findings (2025)**: Incomplete risk assessments (42%), inadequate bias testing (38%), missing impact assessments (35%), insufficient monitoring (31%), poor documentation (29%).

**SOC 2 Type II for AI**: Verifies model versioning, training data governance, and drift monitoring operated effectively over 6-12 months. In 2026, enterprise buyers treat it as a procurement baseline ([TechAhead](https://www.techaheadcorp.com/blog/soc-2-ai-systems-controls/)).

### 9.5 Framework Interrelationships

| Dimension | EU AI Act | NIST AI RMF | ISO/IEC 42001 |
|---|---|---|---|
| **Nature** | Mandatory law | Voluntary risk management | Voluntary certifiable standard |
| **Scope** | AI products on EU market | Operational risk management | AI management system |
| **Enforcement** | Legal penalties (up to 7% turnover) | Regulatory reference | Certification audit |
| **Focus** | Risk-based regulation | Risk identification and management | Organizational governance |

**Recommended implementation path** (most US organizations): NIST AI RMF for risk management (3-6 months) -> ISO 42001 for certification (2-4 months additional) -> EU AI Act compliance if European exposure (2-4 months additional) ([NeuralTrust](https://neuraltrust.ai/blog/ai-governance-framework-comparison)).

**Key stat**: Organizations with AI governance platforms are 3.4x more likely to reach high-value AI outcomes (Gartner, 2025). The global AI governance market was valued at $308.3M in 2025, projected to reach $3.59B by 2033 (CAGR 36%) ([Blaxel Blog](https://blaxel.ai/blog/soc-2-compliance-ai-guide)).

---

## 10. Zero-Trust Architecture for AI

### 10.1 Industry Convergence

At RSAC 2026, four major vendors (Microsoft, Cisco, CrowdStrike, Splunk) independently concluded: Zero Trust must extend to every AI workload, every agent identity, and every model interaction. ZTA is already embraced by 63% of organizations ([NeuralCoreTech](https://neuralcoretech.com/zero-trust-architecture-ai-applications-2026/)).

### 10.2 Microsoft Zero Trust for AI (March 2026)

Three core principles adapted for AI ([Microsoft Security Blog](https://www.microsoft.com/en-us/security/blog/2026/03/19/new-tools-and-guidance-announcing-zero-trust-for-ai/)):
1. **Verify explicitly**: Continuously evaluate identity and behavior of AI agents, workloads, and users
2. **Apply least privilege**: Restrict access to models, prompts, plugins, and data sources to only what is needed
3. **Assume breach**: Design AI systems resilient to prompt injection, data poisoning, and lateral movement

### 10.3 Why Traditional Security Falls Short for AI

- A human employee may access a few systems. An AI assistant could be authorized to access customer records, HR files, financial documents, legal files, and engineering repos simultaneously
- AI systems operate at machine speed and scale -- a single unmanaged access could expose millions of data points in seconds
- Agents chain actions together, calling APIs and orchestrating workflows across multiple systems
- Prompt injection creates a fundamentally new attack vector where the AI itself becomes the exploited component
([CSA](https://cloudsecurityalliance.org/artifacts/using-zero-trust-to-secure-enterprise-information-in-llm-environments))

### 10.4 Architectural Approaches

**Enclave architecture**: A coding agent working on project A should access only project A's files, tools, and LLM endpoints -- not project B's, even if both belong to the same team. The enclave defines the reachable destination set. Everything outside is unreachable ([Zentera](https://www.zentera.net/blog/zero-trust-architecture-for-agentic-ai)).

**AI Session Controller (ASC)**: Inline TLS-terminating proxy between agent and LLM/MCP server/API endpoint. Governs what happens within enclave boundaries at the session layer ([Zentera](https://www.zentera.net/blog/zero-trust-architecture-for-agentic-ai)).

**Agent identity**: AI agents as first-class identities with lifecycle governance, accountability, and context-awareness. Authentication via mTLS, JWTs, OAuth client credentials, TPM-protected secrets ([Preprints.org](https://www.preprints.org/manuscript/202602.0085)).

### 10.5 Gartner Forecast

40% of enterprise applications will include task-specific AI agents by end of 2026, up from less than 5% in 2025. This explosive growth makes Zero Trust for AI urgent rather than aspirational ([NeuralCoreTech](https://neuralcoretech.com/zero-trust-architecture-ai-applications-2026/)).

### 10.6 Key Standards for AI Zero Trust

- NIST AI RMF for governance alignment
- ISO/IEC 42001:2023 for organizational controls
- OWASP Top 10 for Agentic Applications (December 2025) for agent-specific mitigations
- Cloud Security Alliance Agentic Trust Framework (ATF, February 2026) for autonomous agent governance
([ISACA](https://www.isaca.org/resources/news-and-trends/industry-news/2026/preparing-zero-trust-for-ai-disruption))

---

## 11. Real-World Incidents & Financial Impact

### 11.1 Major Incidents (2024-2025)

| Incident | Date | Impact |
|---|---|---|
| **DeepSeek database exposure** | Jan 2025 | 1M+ log entries including chat histories, API keys, backend data exposed via misconfigured ClickHouse DB |
| **OmniGPT breach** | Feb 2025 | 30,000+ users' emails, API keys, credentials leaked. 34M+ lines of chat conversations compromised |
| **Google Vertex AI privilege escalation** | 2025 | Enabled model theft and customer data theft |
| **Cursor IDE RCE** | 2025 | CVE-2025-54135/54136: prompt injection -> remote code execution |
| **Hugging Face malicious models** | 2024 | ~100 models with code execution payloads via pickle deserialization |
| **Air Canada chatbot liability** | Feb 2024 | Chatbot promised non-existent refund; tribunal ruled airline liable for AI output |
| **OpenAI fined by Italy** | Dec 2024 | EUR 15M ($17M) for GDPR violations and failure to protect children |
| **Ultralytics PyPI compromise** | Dec 2024 | GitHub Actions cache-poisoning injected crypto miner into package |

([NSFOCUS](https://nsfocusglobal.com/the-invisible-battlefield-behind-llm-security-crisis/), [Oligo Security](https://www.oligo.security/academy/llm-security-in-2025-risks-examples-and-best-practices), [Check Point](https://www.checkpoint.com/cyber-hub/what-is-llm-security/llm-security-risks/))

### 11.2 Financial Impact Statistics

- **IBM Cost of a Data Breach Report 2024**: Average cost reached all-time high of **$4.88 million**
- **67% of organizations** deploying LLMs reported at least one security incident in the past year, yet only **24% had dedicated AI security policies** (IBM/Ponemon 2024)
- Gartner predicts **30% of GenAI projects** will be abandoned after PoC by end of 2025, with inadequate risk controls cited as a key factor
- Most incidents were not novel exploits but misconfigurations, missing guardrails, or AI features connected to data systems without proper access controls
([Lasso Security](https://lasso.security/blog/llm-risks-enterprise-threats), [Cobalt Blog](https://www.cobalt.io/blog/llm-failures-large-language-model-security-risks))

### 11.3 Key Patterns

- LLM breaches often present as access control failures and data boundary failures, with the LLM acting as an amplifier or a new path to sensitive systems
- A successful jailbreak on an agentic system is not just an embarrassing screenshot -- it is a pathway to remote code execution, data exfiltration, and full system compromise
- The 2025 breach cluster (5 major breaches in January-February alone) signals rising incident tempo and recurring patterns of weak access controls around LLM inputs/outputs
([AINEWSHub](https://www.ainewshub.org/post/llm-security-cyber-battleground))

---

## 12. Key Concepts for Interviews

### 12.1 Attack Taxonomy Quick Reference

| Attack Type | Vector | Defense |
|---|---|---|
| Direct prompt injection | User input | Input classifiers, instruction hierarchy |
| Indirect prompt injection | External content (docs, web, email) | Content sanitization, data/instruction separation |
| GCG adversarial suffixes | Gradient-optimized tokens | Perplexity filtering, random ablation |
| Many-shot jailbreaking | Extended context exploitation | Context window management, in-context defense |
| Crescendo | Multi-turn gradual escalation | Multi-turn conversation monitoring |
| Skeleton Key | Direct behavior guideline modification | System prompt hardening, output classifiers |
| Multimodal injection | Images, audio, video with embedded text | Multimodal content scanning |
| Tool poisoning | Compromised tool descriptions/schemas | Tool integrity verification (mcp-scan) |
| Training data extraction | Targeted queries | Differential privacy, output filtering |
| Data poisoning | Corrupted training data | Data provenance, validation pipelines |

### 12.2 Defense Layers Summary

```
Layer 0: Input Canonicalization (encoding normalization)
Layer 1: Edge Protection (API gateway, rate limiting, auth)
Layer 2: Semantic Firewall (injection detection, topic boundaries, PII)
Layer 3: Context Management (conversation state, system prompt isolation)
Layer 4: Model-Level Safety (alignment, circuit breakers, RepE steering)
Layer 5: Output Pipeline (content filtering, data leakage detection)
Layer 6: Operational Guards (monitoring, circuit breakers, kill switches)
Layer 7: Human Oversight (HITL for high-impact actions)
```

### 12.3 Framework Comparison for Practitioners

| Need | Solution |
|---|---|
| Conversation flow control + jailbreak prevention | NeMo Guardrails |
| Output validation + structured data | Guardrails AI |
| Comprehensive safety classification | Granite Guardian |
| Multimodal content moderation | Llama Guard 4 |
| Constitutional safety enforcement | Anthropic Constitutional Classifiers |
| PII detection & redaction | Presidio + domain-specific recognizers |
| Automated red teaming in CI/CD | Promptfoo |
| Research-grade safety benchmarking | HarmBench |
| Supply chain provenance | Cisco Model Provenance Kit + CycloneDX ML-BOM |
| Agent security scanning | mcp-scan + OWASP MCP Top 10 alignment |

### 12.4 Critical Numbers to Know

- OWASP LLM Top 10 2025: 10 categories, 3 new (System Prompt Leakage, Vector/Embedding Weaknesses, Unbounded Consumption)
- OWASP MCP Top 10: 10 categories covering MCP protocol security. CVE-2025-6514 scored CVSS 9.6
- GCG transfer ASR: 47-87% on GPT-3.5, 29-47% on GPT-4, 2% on Claude-2
- Constitutional Classifiers: reduced jailbreak ASR from 86% to 4.4% (first gen)
- Granite Guardian: 86% on GuardBench (top score across 40 datasets)
- TRYLOCK: 88% relative ASR reduction on Mistral-7B
- Data poisoning: ~250 documents sufficient regardless of model size; 0.1% training data sufficient for backdoor
- NIST red teaming: agent-specific attacks raised hijacking success from 11% to 81%
- IBM 2024: 67% of LLM-deploying orgs reported security incidents; only 24% had AI security policies
- EU AI Act: up to 7% global turnover in fines
- Gartner: 40% of enterprise apps will include AI agents by end of 2026

---

## Sources

### OWASP Frameworks
1. [OWASP Top 10 for LLM Applications](https://owasp.org/www-project-top-10-for-large-language-model-applications/)
2. [OWASP GenAI Security Project - LLM Top 10](https://genai.owasp.org/llm-top-10/)
3. [OWASP LLM01:2025 Prompt Injection](https://genai.owasp.org/llmrisk/llm01-prompt-injection/)
4. [OWASP Top 10 for LLMs 2025 PDF](https://owasp.org/www-project-top-10-for-large-language-model-applications/assets/PDF/OWASP-Top-10-for-LLMs-v2025.pdf)
5. [OWASP MCP Top 10](https://owasp.org/www-project-mcp-top-10/)
6. [OWASP MCP Top 10 GitHub](https://github.com/OWASP/www-project-mcp-top-10)
7. [OWASP MCP Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/MCP_Security_Cheat_Sheet.html)
8. [OWASP Top 10 for Agentic Applications 2026](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/)
9. [OWASP LLM Prompt Injection Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html)

### Prompt Injection & Jailbreaking
10. [Universal Adversarial Suffixes: GCG Attack - JailbreakDB](https://www.jailbreakdb.com/posts/universal-adversarial-suffixes-gcg/)
11. [Many-Shot Jailbreaking - Anthropic](https://www-cdn.anthropic.com/af5633c94ed2beb282f6a53c595eb437e8e7b630/Many_Shot_Jailbreaking__2024_04_02_0936.pdf)
12. [Crescendo Multi-Turn Jailbreak - arXiv](https://arxiv.org/abs/2404.01833)
13. [Crescendo - USENIX Security 2025](https://www.usenix.org/conference/usenixsecurity25/presentation/russinovich)
14. [Skeleton Key - Microsoft Security Blog](https://www.microsoft.com/en-us/security/blog/2024/06/26/mitigating-skeleton-key-a-new-type-of-generative-ai-jailbreak-technique/)
15. [Prompt Injection Survey - ScienceDirect](https://www.sciencedirect.com/org/science/article/pii/S1546221826001384)
16. [Prompt Injection on Agentic Coding Assistants - arXiv](https://arxiv.org/html/2601.17548v1)
17. [AI Jailbreak Techniques 2026 - ZioSec](https://ziosec.com/blog/ai-jailbreak-techniques-in-2026-a-complete-technical-guide-ziosec)

### Guardrail Systems
18. [Guardian Models Overview - TuringPost](https://www.turingpost.com/p/guardianmodels)
19. [Llama Safety and Guardrails - TechJack Solutions](https://techjacksolutions.com/ai-tools/meta-llama/mastering-llama-safety-and-guardrails/)
20. [LLM Guardrails Production Reference 2026 - Digital Applied](https://www.digitalapplied.com/blog/llm-guardrails-production-safety-layers-reference-2026)
21. [Ultimate Guide to LLM Guardrails 2026 - FutureAGI](https://futureagi.com/blog/ultimate-guide-llm-guardrails-2026/)
22. [Granite Guardian - IBM Research](https://research.ibm.com/blog/granite-guardian-tops-guardbench)
23. [Granite Guardian NAACL 2025 - ACL Anthology](https://aclanthology.org/2025.naacl-industry.49/)
24. [Constitutional Classifiers - Anthropic](https://www.anthropic.com/research/constitutional-classifiers)
25. [Next-Gen Constitutional Classifiers - Anthropic](https://www.anthropic.com/research/next-generation-constitutional-classifiers)
26. [ASL-3 Deployment Safeguards - Anthropic](https://www.anthropic.com/asl3-deployment-safeguards)
27. [NeMo Guardrails - NVIDIA Docs](https://docs.nvidia.com/nemo/guardrails/about-nemo-guardrails-library/overview)
28. [NeMo Guardrails - GitHub](https://github.com/NVIDIA-NeMo/Guardrails)
29. [Guardrails AI + NeMo Integration](https://guardrailsai.com/blog/nemoguardrails-integration)
30. [Guardrails AI vs NeMo Guardrails 2026 - is4.ai](https://is4.ai/blog/our-blog-1/guardrails-ai-vs-nemo-guardrails-comparison-2026-352)
31. [F5 + NeMo Guardrails Integration - TheFastMode](https://www.thefastmode.com/technology-solutions/49973-f5-integrates-ai-guardrails-with-nvidia-nemo-guardrails-to-strengthen-enterprise-ai-security)

### Red Teaming
32. [NIST AI 600-1 PDF](https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf)
33. [CSA Research Note - NIST AI Agent Red Teaming](https://labs.cloudsecurityalliance.org/research/csa-research-note-nist-ai-agent-red-teaming-standards-202603/)
34. [Promptfoo - GitHub](https://github.com/promptfoo/promptfoo)
35. [Promptfoo Red Team Docs](https://www.promptfoo.dev/docs/red-team/)
36. [Promptfoo - NVISO Blog](https://blog.nviso.eu/2026/02/05/an-introduction-to-automated-llm-red-teaming/)
37. [HarmBench - GitHub](https://github.com/centerforaisafety/HarmBench)
38. [AI Red Teaming Guide 2026 - Mindgard](https://mindgard.ai/blog/what-is-ai-red-teaming)

### Agent Security
39. [OWASP Top 10 for Agentic Applications - Cycode](https://cycode.com/blog/owasp-top-10-agentic-applications/)
40. [OWASP MCP Top 10 - PipeLab](https://pipelab.org/learn/owasp-mcp-top10/)
41. [OWASP MCP Top 10 - Practical DevSecOps](https://www.practical-devsecops.com/owasp-mcp-top-10/)
42. [Tool Poisoning and MCP Threat Modeling - arXiv](https://arxiv.org/html/2603.22489v1)
43. [CSA Agentic MCP Security Best Practices](https://labs.cloudsecurityalliance.org/agentic/agentic-mcp-security-best-practices-v1/)
44. [OWASP Agentic Top 10 - Authensor](https://www.authensor.com/updates/owasp-agentic-top-10-explained)

### Content Safety
45. [PII Redaction for LLM Prompts - Wavect](https://wavect.io/blog/pii-redaction-before-llm-prompts/)
46. [PII Detection Guide - Prediction Guard](https://predictionguard.com/blog/pii-detection-redaction-llm-pipelines-regulated-industries)
47. [Presidio Introduction - Ploomber](https://ploomber.io/blog/presidio/)
48. [PII Prevention in AI - Gravitee](https://www.gravitee.io/blog/how-to-prevent-pii-leaks-in-ai-systems-automated-data-redaction-for-llm-prompt)
49. [LiteLLM Guardrails](https://docs.litellm.ai/docs/proxy/guardrails/quick_start)
50. [Hallucination Survey - arXiv](https://arxiv.org/html/2510.06265v2)
51. [Hallucination Mitigation Taxonomy - MDPI](https://www.mdpi.com/2673-2688/6/10/260)
52. [Hallucination Detection Guide - Lakera](https://www.lakera.ai/blog/guide-to-hallucinations-in-large-language-models)
53. [Debiasing and Dehallucinating Review - Springer](https://link.springer.com/article/10.1007/s10462-024-10896-y)

### Defense Architecture
54. [End-to-End LLM Security Architecture - IOSEC.IN](https://iosec.in/end-to-end-llm-security-architecture/)
55. [Securing AI APIs: Defense-in-Depth - Secure By Dezign](https://www.securebydezign.com/articles/securing-ai-apis-beyond-rate-limiting.html)
56. [LLM Guardrails Complete Guide - AI Safety Directory](https://aisecurityandsafety.org/en/guides/llm-guardrails/)
57. [Model Armor: Multi-Layer Safety Filtering - InDepth.dev](https://indepth.dev/posts/2013/en/building-model-armor-multi-layer-llm-safety-filtering)
58. [TRYLOCK - arXiv](https://arxiv.org/abs/2601.03300)
59. [Circuit Breakers for AI - arXiv](https://arxiv.org/pdf/2406.04313)
60. [Prompt Injection Defense for Production - Introl](https://introl.com/blog/llm-security-prompt-injection-defense-production-guide-2025)

### Supply Chain Security
61. [AI Supply Chain Security - SafeGuard.sh](https://safeguard.sh/resources/blog/overview-of-ai-model-supply-chain-security-risks-end-to-end)
62. [AI Supply Chain Security Guide - GLACIS](https://www.glacis.io/guide-ai-supply-chain-security)
63. [Stop Model Poisoning - Polygraf AI](https://www.polygraf.ai/blogposts/ai-supply-chain-security-stop-model-poisoning/)
64. [Supply Chain Poisoning Deep Dive - TechBytes](https://techbytes.app/posts/supply-chain-poisoning-in-ai-models-deep-dive-2026/)
65. [AI-BOMs Replace SBOMs - The Register](https://www.theregister.com/2026/05/04/ai_bom_supply_chain/)
66. [AI Supply Chain Security - Pharos Production](https://pharosproduction.com/insights/engineering/ai-supply-chain-security-2026/)
67. [AI Supply Chain Attacks - Infosec.qa](https://infosec.qa/blog/ai-supply-chain-attacks/)
68. [Training Data Poisoning and Supply Chain Risk - CISO Marketplace](https://cisomarketplace.com/blog/ai-model-supply-chain-training-data-poisoning-open-source-risk)

### Compliance Frameworks
69. [EU AI Act vs NIST vs ISO 42001 - EC Council](https://www.eccouncil.org/cybersecurity-exchange/responsible-ai-governance/eu-ai-act-nist-ai-rmf-and-iso-iec-42001-a-plain-english-comparison/)
70. [AI Governance Frameworks Compared - Compyl](https://compyl.com/blog/ai-governance-frameworks-compared-nist-iso-42001-eu-ai-act/)
71. [Global AI Governance Comparison 2026 - GAICC](https://gaicc.org/blog/ai-governance-comparison-eu-ai-act-nist-iso-42001/)
72. [ISO 42001 and EU AI Act Mapping - Truvo Cyber](https://truvocyber.com/blog/iso-42001-and-eu-ai-act)
73. [ISO 42001 + NIST for EU AI Act Compliance - CSA](https://cloudsecurityalliance.org/blog/2025/01/29/how-can-iso-iec-42001-nist-ai-rmf-help-comply-with-the-eu-ai-act)
74. [EU AI Act Deadlines and Compliance - Orbit Reconn](https://orbit.reconn.io/eu-ai-act/)
75. [SOC 2 for AI Companies 2026 - SOC2 Auditors](https://soc2auditors.org/insights/soc-2-for-ai-companies/)
76. [SOC 2 AI Controls - Baker Tilly](https://www.bakertilly.com/insights/ai-controls-for-soc-2-reports)
77. [SOC 2 AI Systems Controls - TechAhead](https://www.techaheadcorp.com/blog/soc-2-ai-systems-controls/)
78. [AI Governance for SOC 2 - Security Boulevard](https://securityboulevard.com/2026/07/5-ai-governance-frameworks-for-soc-2-compliance/)
79. [SOC 2 Compliance for AI Agents - Blaxel](https://blaxel.ai/blog/soc-2-compliance-ai-guide)

### Zero Trust for AI
80. [Zero Trust AI Security 2026 - NeuralCoreTech](https://neuralcoretech.com/zero-trust-architecture-ai-applications-2026/)
81. [Zero Trust for AI - Microsoft Security Blog](https://www.microsoft.com/en-us/security/blog/2026/03/19/new-tools-and-guidance-announcing-zero-trust-for-ai/)
82. [Zero Trust in LLM Environments - CSA](https://cloudsecurityalliance.org/artifacts/using-zero-trust-to-secure-enterprise-information-in-llm-environments)
83. [Zero Trust for Agentic AI - Zentera](https://www.zentera.net/blog/zero-trust-architecture-for-agentic-ai)
84. [Zero Trust for AI Disruption - ISACA](https://www.isaca.org/resources/news-and-trends/industry-news/2026/preparing-zero-trust-for-ai-disruption)

### Real-World Incidents
85. [LLM Security Crisis - NSFOCUS](https://nsfocusglobal.com/the-invisible-battlefield-behind-llm-security-crisis/)
86. [LLM Security 2025 - Oligo Security](https://www.oligo.security/academy/llm-security-in-2025-risks-examples-and-best-practices)
87. [LLM Security Risks - Check Point](https://www.checkpoint.com/cyber-hub/what-is-llm-security/llm-security-risks/)
88. [LLM Risks: Enterprise Threats - Lasso Security](https://lasso.security/blog/llm-risks-enterprise-threats)
89. [LLM Failures - Cobalt](https://www.cobalt.io/blog/llm-failures-large-language-model-security-risks)
90. [LLM Security: Cyber Battleground - AINewsHub](https://www.ainewshub.org/post/llm-security-cyber-battleground)

### Additional Research
91. [Awesome MLLM Guardrails - GitHub](https://github.com/ant-research/awesome-mllm-guardrails)
92. [AI Red Teaming Guide - GitHub](https://github.com/requie/AI-Red-Teaming-Guide)
93. [Representation Engineering to Circuit Breaking - CMU Blog](https://www.cs.cmu.edu/~csd-phd-blog/2025/representation-engineering/)
94. [Circuit Breakers for AI Agents - NeuralTrust](https://neuraltrust.ai/blog/circuit-breakers)
95. [LLM Security and Guardrails - Langfuse Docs](https://langfuse.com/docs/security-and-guardrails)
96. [Automated Prompt Injection in Agentic Environments - arXiv](https://arxiv.org/html/2606.10525)
97. [AI Governance Frameworks - NeuralTrust](https://neuraltrust.ai/blog/ai-governance-framework-comparison)
98. [Zero Trust for AI Reference Architecture - Preprints.org](https://www.preprints.org/manuscript/202602.0085)
