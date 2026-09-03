"""
Deep Agents Execution Environment — Interview Prep Code Examples

Covers the execution layer of LangChain Deep Agents >= 0.7.x:
- Namespace isolation patterns for StoreBackend (per-user, per-thread, combined)
- Complete production execution environment setup with CompositeBackend,
  sandbox, permissions, interpreter, and MCP integration
- Custom backend implementation (S3) showing the BackendProtocol contract
- Production sandbox with circuit breaker, PII detection/redaction/audit,
  composite routing, and LocalShell prohibition

Source: 09-deep-agents-execution.md
"""


# --- Section: Core Concepts & Algorithms > Pluggable Backends > Namespace Isolation in StoreBackend ---

# Per-user isolation (most common for multi-tenant):
namespace = lambda rt: (rt.server_info.user.identity,)

# Per-thread isolation (conversation-scoped):
namespace = lambda rt: (rt.execution_info.thread_id,)

# Combined (user + thread + domain):
namespace = lambda rt: (
    rt.server_info.user.identity,
    rt.execution_info.thread_id,
    "filesystem",
)


# --- Section: Code Examples > Complete Production Execution Environment Setup ---

"""
Production execution environment with CompositeBackend, sandbox,
permissions, interpreter, and MCP integration.

Requirements:
  pip install deepagents langgraph-checkpoint-postgres
  pip install langchain-mcp-adapters e2b-code-interpreter
"""

import logging
from typing import Any

from deepagents import create_deep_agent
from deepagents.backends import (
    CompositeBackend,
    FilesystemBackend,
    StateBackend,
    StoreBackend,
)
from deepagents.interpreter import InterpreterMiddleware
from deepagents.middleware import (
    HumanInTheLoopMiddleware,
    SummarizationMiddleware,
    ToolCallLimitMiddleware,
)
from deepagents.permissions import FilesystemPermission
from deepagents.sandboxes import E2BSandboxBackend
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.store.postgres import PostgresStore

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_URI = "postgresql://agent_user:secure_pass@db-host:5432/agent_state"

checkpointer = PostgresSaver.from_conn_string(DB_URI)
checkpointer.setup()

store = PostgresStore.from_conn_string(DB_URI)
store.setup()

# 1. Sandbox backend -- Firecracker microVM via E2B
sandbox_backend = E2BSandboxBackend(
    api_key="e2b_api_key_from_vault",  # from secrets manager, never hardcoded
    template="python-3.12",
    idle_ttl_seconds=3600,
    timeout=120,
    max_output_bytes=100_000,
)

# 2. CompositeBackend -- route user workspace to sandbox, memories to store
backend = CompositeBackend(
    default=StateBackend(),  # internal data: summaries, tool results
    routes={
        "/workspace/": sandbox_backend,
        "/memories/": StoreBackend(
            store=store,
            namespace=lambda rt: (
                rt.server_info.user.identity,
                "memories",
            ),
        ),
        "/shared/": FilesystemBackend(
            root_dir="./shared_resources",
            virtual_mode=True,
        ),
    },
)

# 3. Permissions -- defense in depth
permissions = [
    # Block all secret files everywhere
    FilesystemPermission(
        operations=["read", "write"],
        paths=["/**/.env", "/**/.env.*", "/**/credentials*", "/**/*.key", "/**/*.pem"],
        mode="deny",
    ),
    # Full access to user workspace (sandbox-backed)
    FilesystemPermission(
        operations=["read", "write"],
        paths=["/workspace/**"],
        mode="allow",
    ),
    # Read-only access to shared resources
    FilesystemPermission(
        operations=["read"],
        paths=["/shared/**"],
        mode="allow",
    ),
    FilesystemPermission(
        operations=["write"],
        paths=["/shared/**"],
        mode="deny",
    ),
    # Human approval for memory writes
    FilesystemPermission(
        operations=["read"],
        paths=["/memories/**"],
        mode="allow",
    ),
    FilesystemPermission(
        operations=["write"],
        paths=["/memories/**"],
        mode="interrupt",
    ),
    # Deny everything else
    FilesystemPermission(
        operations=["read", "write"],
        paths=["/**"],
        mode="deny",
    ),
]

# 4. Interpreter -- QuickJS with PTC for batched operations
interpreter_mw = InterpreterMiddleware(
    mode="thread",
    memory_limit=64 * 1024 * 1024,  # 64 MB heap
    timeout=5.0,
    max_result_chars=4000,
    max_ptc_calls=256,
    ptc_allowlist=[
        "read_file",
        "grep",
        "glob",
        "search_docs",
    ],
    # NOTE: do NOT add write_file, delete, or execute to ptc_allowlist
    # PTC calls bypass HITL interrupt_on checks
)

# 5. Assemble the agent
agent = create_deep_agent(
    model="anthropic:claude-sonnet-4-6",
    tools=[search_docs, deploy_service],
    system_prompt="You are a senior DevOps engineer assistant...",
    middleware=[
        SummarizationMiddleware(trigger=("tokens", 80_000), retention=("messages", 15)),
        ToolCallLimitMiddleware(max_calls=150),
        interpreter_mw,
    ],
    backend=backend,
    permissions=permissions,
    memory="./AGENTS.md",
    interrupt_on={"tools": ["deploy_service"]},
    checkpointer=checkpointer,
    store=store,
)


# --- Section: Code Examples > Custom Backend Implementation (S3) ---

"""Custom backend wrapping S3. Shows the BackendProtocol contract.
Key rule: return structured dicts with 'error' field -- never raise exceptions."""

import boto3
from typing import Any

class S3Backend:
    def __init__(self, bucket: str, prefix: str = "agent-files/"):
        self.bucket = bucket
        self.prefix = prefix
        self.s3 = boto3.client("s3")

    def _key(self, path: str) -> str:
        return f"{self.prefix}{path.lstrip('/')}"

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> dict[str, Any]:
        try:
            obj = self.s3.get_object(Bucket=self.bucket, Key=self._key(file_path))
            content = obj["Body"].read().decode("utf-8")
            lines = content.splitlines(keepends=True)
            return {"content": "".join(lines[offset : offset + limit]), "error": ""}
        except self.s3.exceptions.NoSuchKey:
            return {"content": "", "error": f"File not found: {file_path}"}

    def write(self, file_path: str, content: str) -> dict[str, Any]:
        self.s3.put_object(Bucket=self.bucket, Key=self._key(file_path), Body=content.encode())
        return {"error": ""}

    def edit(self, file_path: str, old_string: str, new_string: str,
             replace_all: bool = False) -> dict[str, Any]:
        result = self.read(file_path, offset=0, limit=100_000)
        if result["error"]:
            return result
        content = result["content"]
        if old_string not in content:
            return {"error": f"String not found in {file_path}"}
        updated = (content.replace(old_string, new_string) if replace_all
                   else content.replace(old_string, new_string, 1))
        return self.write(file_path, updated)


# --- Section: Code Examples > Production Sandbox + Circuit Breaker + PII Pipeline ---

#!/usr/bin/env python3
"""Execution data plane: sandbox execute, MCP PEP, VFS composite, stdlib fallbacks.

Fallback: sandbox 503 -> queue; MCP down -> disable those tools.
NEVER fail-open to LocalShell / unsandboxed execute.
"""
from __future__ import annotations
import hashlib, json, logging, random, re, time, uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

# --- retries + full jitter ---
def retry_call(fn, *, attempts=3, base_s=0.2, cap_s=2.0,
               retryable=(TimeoutError, ConnectionError)):
    last = None
    for i in range(attempts):
        try:
            return fn()
        except retryable as exc:
            last = exc
            if i == attempts - 1:
                break
            sleep_s = random.random() * min(cap_s, base_s * (2**i))
            time.sleep(sleep_s)
    raise last

# --- circuit breaker ---
class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

@dataclass
class CircuitBreaker:
    name: str
    failure_threshold: int = 3
    cooldown_s: float = 30.0
    _state: CircuitState = CircuitState.CLOSED
    _failures: int = 0
    _opened_at: float = 0.0

    def allow(self):
        if self._state is CircuitState.OPEN:
            if time.monotonic() - self._opened_at >= self.cooldown_s:
                self._state = CircuitState.HALF_OPEN
            else:
                raise RuntimeError(f"circuit_open:{self.name}")

    def record_success(self):
        self._failures = 0
        self._state = CircuitState.CLOSED

    def record_failure(self):
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()

# --- PII: detect -> redact -> audit ---
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
PAN_RE = re.compile(r"\b(?:\d[ -]*?){13,19}\b")

def pii_detect_redact_audit(text, *, audit, correlation_id, tenant_id,
                             sink, block_on_pan=True):
    kinds = []
    if EMAIL_RE.search(text): kinds.append("email")
    if PAN_RE.search(text): kinds.append("pan")
    pre = hashlib.sha256(text.encode()).hexdigest()
    if "pan" in kinds and block_on_pan and sink in {"mcp_args", "execute_env", "vfs_write"}:
        audit.append({"cid": correlation_id, "sink": sink, "action": "block"})
        raise PermissionError(f"pii_block:{sink}:pan")
    redacted = EMAIL_RE.sub(lambda m: f"[EMAIL_{hashlib.sha256(m.group().encode()).hexdigest()[:12]}]", text)
    redacted = PAN_RE.sub("[PAN]", redacted)
    audit.append({"cid": correlation_id, "sink": sink,
                  "action": "redact" if redacted != text else "allow",
                  "pre": pre, "post": hashlib.sha256(redacted.encode()).hexdigest()})
    return redacted

# --- Composite longest-prefix router ---
@dataclass
class CompositeRouter:
    default: str
    routes: dict[str, str]
    def resolve(self, path: str) -> str:
        hits = [p for p in self.routes if path.startswith(p)]
        return self.routes[max(hits, key=len)] if hits else self.default

# --- Sandbox pool: 503 -> queue; LocalShell is not a port ---
class LocalShellForbidden(RuntimeError):
    """Invariant: circuit-open sandbox MUST NOT fail-open to host shell."""

def local_shell_execute(command):
    raise LocalShellForbidden(f"refused_local_shell:{command[:40]}")
