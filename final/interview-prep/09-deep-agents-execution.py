"""
Deep Agents Execution Environment -- sandbox isolation, virtual filesystem,
permissions, code execution with timeouts, streaming, and resource limits.

The execution environment is the DATA PLANE of an agent system: where files are
read/written, commands run, and results observed. The key design insight is
separation of concerns -- agent file ops are identical regardless of physical
backend (in-memory, disk, cloud store, remote sandbox).
"""

from __future__ import annotations

import io
import os
import signal
import subprocess
import sys
import textwrap
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional


# =============================================================================
# --- Section 1: Virtual Filesystem with Backend Abstraction ------------------
# =============================================================================
# The VFS lets the agent read/write files without knowing where they physically
# live. You swap backends (in-memory, disk, cloud) without changing agent code.


class BackendProtocol:
    """Minimal contract every VFS backend must satisfy.

    Key design rules from Deep Agents:
    - NEVER raise exceptions -- return structured results with an 'error' field.
    - Methods: ls, read, write, edit, delete, glob, grep.
    - Optional: SandboxBackendProtocol adds execute().
    """

    def read(self, path: str, offset: int = 0, limit: int = 100) -> dict:
        raise NotImplementedError

    def write(self, path: str, content: str) -> dict:
        raise NotImplementedError

    def ls(self, path: str) -> dict:
        raise NotImplementedError

    def delete(self, path: str) -> dict:
        raise NotImplementedError


class StateBackend(BackendProtocol):
    """In-memory backend (the default). Ephemeral -- dies with the process
    unless backed by a durable checkpointer.

    Interview point: This is the DEFAULT backend. Files here are just
    key-value pairs in a dict. Good for scratch work, bad for persistence.
    """

    def __init__(self):
        self._store: dict[str, str] = {}

    def read(self, path: str, offset: int = 0, limit: int = 100) -> dict:
        if path not in self._store:
            return {"error": f"File not found: {path}"}
        lines = self._store[path].splitlines()
        page = lines[offset : offset + limit]
        return {
            "content": "\n".join(page),
            "total_lines": len(lines),
            "next_offset": offset + limit if offset + limit < len(lines) else None,
        }

    def write(self, path: str, content: str) -> dict:
        # Since v0.7, write_file OVERWRITES (previously create-only).
        # This is a breaking change interviewers may ask about.
        self._store[path] = content
        return {"status": "ok", "path": path, "bytes": len(content)}

    def ls(self, path: str) -> dict:
        prefix = path.rstrip("/") + "/"
        children = sorted(
            {k[len(prefix) :].split("/")[0] for k in self._store if k.startswith(prefix)}
        )
        # Since v0.7, empty ls returns "No files found" (not []).
        # Do NOT json.loads() the tool string -- it is not valid JSON.
        if not children:
            return {"message": "No files found"}
        return {"entries": children}

    def delete(self, path: str) -> dict:
        if path in self._store:
            del self._store[path]
            return {"status": "ok"}
        return {"error": f"File not found: {path}"}


class FilesystemBackend(BackendProtocol):
    """Disk-backed backend. virtual_mode=True (default since v0.7) jails
    all operations under root_dir.

    Interview point: virtual_mode is an FS-tool jail, NOT a sandbox.
    It prevents path traversal but does not isolate processes.
    """

    def __init__(self, root_dir: str, virtual_mode: bool = True):
        self.root_dir = os.path.abspath(root_dir)
        self.virtual_mode = virtual_mode

    def _resolve(self, path: str) -> str:
        """Resolve a virtual path to a real path, enforcing jail."""
        resolved = os.path.normpath(os.path.join(self.root_dir, path.lstrip("/")))
        if self.virtual_mode and not resolved.startswith(self.root_dir):
            raise ValueError(f"Path escapes root: {path}")
        return resolved

    def read(self, path: str, offset: int = 0, limit: int = 100) -> dict:
        try:
            real = self._resolve(path)
            with open(real) as f:
                lines = f.readlines()
            page = lines[offset : offset + limit]
            return {
                "content": "".join(page),
                "total_lines": len(lines),
                "next_offset": offset + limit if offset + limit < len(lines) else None,
            }
        except Exception as e:
            return {"error": str(e)}

    def write(self, path: str, content: str) -> dict:
        try:
            real = self._resolve(path)
            os.makedirs(os.path.dirname(real), exist_ok=True)
            with open(real, "w") as f:
                f.write(content)
            return {"status": "ok", "path": path}
        except Exception as e:
            return {"error": str(e)}

    def ls(self, path: str) -> dict:
        try:
            real = self._resolve(path)
            entries = sorted(os.listdir(real))
            return {"entries": entries} if entries else {"message": "No files found"}
        except Exception as e:
            return {"error": str(e)}

    def delete(self, path: str) -> dict:
        try:
            real = self._resolve(path)
            os.remove(real)
            return {"status": "ok"}
        except Exception as e:
            return {"error": str(e)}


class CompositeBackend(BackendProtocol):
    """Routes paths to child backends by longest prefix match.

    Interview point: Unmatched paths fall through to DEFAULT -- silent
    persistence bug, not HTTP 404. This is a common source of data loss:
    /memory/foo does not match /memories/ and silently goes to StateBackend.
    """

    def __init__(self, default: BackendProtocol, routes: dict[str, BackendProtocol]):
        self.default = default
        # Sort routes by prefix length descending for longest-match-first
        self.routes = sorted(routes.items(), key=lambda r: len(r[0]), reverse=True)

    def _route(self, path: str) -> tuple[BackendProtocol, str]:
        """Find the backend for a path. Longest prefix wins."""
        for prefix, backend in self.routes:
            if path.startswith(prefix):
                return backend, path
        # No match -> default. THIS IS SILENT. Common bug source.
        return self.default, path

    def read(self, path: str, **kw) -> dict:
        backend, resolved = self._route(path)
        return backend.read(resolved, **kw)

    def write(self, path: str, content: str) -> dict:
        backend, resolved = self._route(path)
        return backend.write(resolved, content)

    def ls(self, path: str) -> dict:
        backend, resolved = self._route(path)
        return backend.ls(resolved)

    def delete(self, path: str) -> dict:
        backend, resolved = self._route(path)
        return backend.delete(resolved)


# =============================================================================
# --- Section 2: Permission Layer (First-Match-Wins, Fail-Open) ---------------
# =============================================================================
# Permissions govern the 8 built-in FS tools ONLY. They do NOT cover MCP,
# execute, task, PTC, or direct backend calls. This is a critical interview
# point -- "fail-open" means no matching rule = allow.


class PermissionMode(Enum):
    ALLOW = "allow"
    DENY = "deny"
    INTERRUPT = "interrupt"  # Requires checkpointer; pauses for human approval


@dataclass
class FilesystemPermission:
    """A single permission rule.

    Evaluation is FIRST-MATCH-WINS, FAIL-OPEN:
    1. Walk rules in declaration order.
    2. First rule whose operation and path pattern match -> use its mode.
    3. No match at all -> ALLOW (this is the fail-open default).

    Security pattern: always end with a deny-all catch-all rule.
    """

    operations: list[str]  # ["read"], ["write"], or ["read", "write"]
    paths: list[str]       # glob patterns like "/workspace/**", "/**/.env"
    mode: str = "allow"    # "allow" | "deny" | "interrupt"


def _path_matches_pattern(path: str, pattern: str) -> bool:
    """Simplified glob match for demonstration.

    Production uses fnmatch-style globs with ** for recursive match.
    """
    import fnmatch
    # Handle ** as recursive wildcard
    if "**" in pattern:
        # Convert ** to match any depth
        regex_pattern = pattern.replace("**", "*")
        return fnmatch.fnmatch(path, regex_pattern)
    return fnmatch.fnmatch(path, pattern)


def check_permission(
    permissions: list[FilesystemPermission],
    operation: str,  # "read" or "write"
    path: str,
) -> str:
    """Evaluate permissions. Returns "allow", "deny", or "interrupt".

    Interview walkthrough:
    - Rules checked top to bottom.
    - First match wins.
    - No match = allow (FAIL-OPEN -- this is the dangerous default).
    - delete is a WRITE operation (since v0.7, breaking change).
    """
    for rule in permissions:
        if operation not in rule.operations:
            continue
        for pattern in rule.paths:
            if _path_matches_pattern(path, pattern):
                return rule.mode
    # No rule matched -- fail-open: allow by default.
    # This is why you MUST have a deny-all catch-all at the end.
    return "allow"


# -- Production permission stack example --
# Order matters! Deny secrets BEFORE allowing workspace.
PRODUCTION_PERMISSIONS = [
    # 1. Block secret files everywhere (must come FIRST)
    FilesystemPermission(
        operations=["read", "write"],
        paths=["/**/.env", "/**/.env.*", "/**/credentials*", "/**/*.key"],
        mode="deny",
    ),
    # 2. Full access to sandbox workspace
    FilesystemPermission(
        operations=["read", "write"],
        paths=["/workspace/**"],
        mode="allow",
    ),
    # 3. Read-only shared resources
    FilesystemPermission(operations=["read"], paths=["/shared/**"], mode="allow"),
    FilesystemPermission(operations=["write"], paths=["/shared/**"], mode="deny"),
    # 4. Human approval for memory writes
    FilesystemPermission(operations=["write"], paths=["/memories/**"], mode="interrupt"),
    # 5. Deny-all catch-all (prevents fail-open)
    FilesystemPermission(operations=["read", "write"], paths=["/**"], mode="deny"),
]


# =============================================================================
# --- Section 3: File Read/Write with Access Control --------------------------
# =============================================================================
# Wraps the VFS + permissions into a single middleware that intercepts all
# file tool calls from the model.


@dataclass
class FilesystemMiddleware:
    """Intercepts model tool calls to enforce permissions before VFS access.

    Interview point: read_file is MANDATORY on any tools= allowlist.
    Omitting it raises ValueError because the offloader needs the model
    to be able to page back large results.

    The 8 built-in FS tools:
    ls, read_file, write_file, edit_file, delete, glob, grep, execute
    """

    backend: BackendProtocol
    permissions: list[FilesystemPermission]

    # Maps tool names to their operation type for permission checking
    READ_OPS = {"ls", "read_file", "glob", "grep"}
    WRITE_OPS = {"write_file", "edit_file", "delete"}

    def handle_tool_call(self, tool_name: str, args: dict) -> dict:
        """Intercept a tool call: check permissions, then dispatch to backend.

        Flow:
        1. Model emits tool_call (e.g., read_file(path="/workspace/main.py"))
        2. Determine operation type (read vs write)
        3. Check permissions (first-match-wins)
        4. If deny -> return error. If interrupt -> pause for human.
        5. Route to backend via CompositeBackend
        6. Return structured result (never raise)
        """
        path = args.get("path", "")

        # Determine operation type
        if tool_name in self.READ_OPS:
            operation = "read"
        elif tool_name in self.WRITE_OPS:
            operation = "write"
        elif tool_name == "execute":
            # execute is NOT covered by permissions= (FS-tools-only).
            # This is a critical security gap interviewers ask about.
            return self._dispatch_execute(args)
        else:
            return {"error": f"Unknown tool: {tool_name}"}

        # Check permissions
        decision = check_permission(self.permissions, operation, path)

        if decision == "deny":
            return {"error": f"Permission denied: {operation} on {path}"}
        if decision == "interrupt":
            return {"status": "interrupted", "message": "Awaiting human approval"}

        # Dispatch to backend
        if tool_name == "read_file":
            return self.backend.read(path, offset=args.get("offset", 0),
                                     limit=args.get("limit", 100))
        elif tool_name == "write_file":
            return self.backend.write(path, args.get("content", ""))
        elif tool_name == "delete":
            return self.backend.delete(path)
        elif tool_name == "ls":
            return self.backend.ls(path)
        else:
            return {"error": f"Unimplemented tool: {tool_name}"}

    def _dispatch_execute(self, args: dict) -> dict:
        """Execute is only available on SandboxBackendProtocol backends.

        If the backend doesn't support execute, the tool is CAPABILITY-HIDDEN:
        the agent never even sees it in the schema. If somehow invoked anyway,
        return an error ToolMessage (don't raise).
        """
        if not hasattr(self.backend, "execute"):
            return {"error": "execute not available on this backend"}
        return self.backend.execute(args.get("command", ""), timeout=args.get("timeout", 120))


# =============================================================================
# --- Section 4: Sandbox Execution with Timeout & Resource Limits -------------
# =============================================================================
# Production = remote sandbox (Firecracker microVM, gVisor, or managed).
# NEVER use LocalShellBackend in production -- it is subprocess.run(shell=True)
# with no isolation.
#
# Three-tier isolation model:
#   Tier 3: MicroVMs (Firecracker) -- gold standard. Own kernel. ~125ms boot.
#   Tier 2: User-space kernels (gVisor) -- syscall interception. Less overhead.
#   Tier 1: Containers (Docker/runc) -- shared kernel. INSUFFICIENT for AI agents.


@dataclass
class ExecuteResponse:
    """Structured result from sandbox command execution."""

    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False
    truncated: bool = False


class SandboxBackend(BackendProtocol):
    """Simulates a remote sandbox backend (e.g., E2B, Modal, LangSmith).

    In production, this would be a Firecracker microVM or gVisor container
    reached over HTTP. Here we simulate the interface and resource limits.

    Key design: agent process stays on YOUR server; API keys stay OUTSIDE
    the sandbox. The sandbox is a tool, not the agent's home.
    """

    MAX_EXECUTE_TIMEOUT = 3600  # Hard cap: 1 hour (<=0 raises ValueError)
    DEFAULT_TIMEOUT = 120       # 2 minutes
    MAX_OUTPUT_BYTES = 100_000  # Truncate stdout/stderr beyond this

    def __init__(
        self,
        idle_ttl_seconds: int = 600,
        memory_limit_mb: int = 2048,   # ~2 GiB (LangSmith default)
        cpu_vcpu: float = 0.5,         # LangSmith default
    ):
        self.idle_ttl_seconds = idle_ttl_seconds
        self.memory_limit_mb = memory_limit_mb
        self.cpu_vcpu = cpu_vcpu
        self._fs: dict[str, str] = {}  # Simulated guest filesystem
        self._last_access = time.time()

    def execute(self, command: str, *, timeout: int | None = None) -> dict:
        """Run a command in the sandbox with timeout enforcement.

        Production flow:
        1. Command sent to isolated environment (Firecracker/gVisor).
        2. Timeout capped at max_execute_timeout (3600s). <=0 is ValueError.
        3. Returns: stdout/stderr, exit_code (on artifact >=0.7.4),
           truncation flag.
        4. Output exceeding caps auto-saved to sandbox artifact path.

        NEVER put secrets in the sandbox. Use auth proxy pattern instead.
        """
        if timeout is not None and timeout <= 0:
            return {"error": "ValueError: timeout must be > 0"}

        effective_timeout = min(timeout or self.DEFAULT_TIMEOUT, self.MAX_EXECUTE_TIMEOUT)
        self._last_access = time.time()

        # In production, this would be an RPC to the sandbox provider.
        # Here we simulate with subprocess for demonstration purposes only.
        # IMPORTANT: This simulation is for learning -- production uses
        # remote sandboxes, never local subprocess.
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=effective_timeout,
            )
            stdout = result.stdout
            stderr = result.stderr
            exit_code = result.returncode
            timed_out = False
        except subprocess.TimeoutExpired:
            stdout = ""
            stderr = f"Command timed out after {effective_timeout}s"
            exit_code = -1
            timed_out = True

        # Truncate output if it exceeds the cap
        truncated = False
        if len(stdout) > self.MAX_OUTPUT_BYTES:
            stdout = stdout[: self.MAX_OUTPUT_BYTES]
            truncated = True

        return {
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "truncated": truncated,
        }

    def read(self, path: str, offset: int = 0, limit: int = 100) -> dict:
        """FS tools in sandbox are implemented ON TOP of execute()."""
        if path in self._fs:
            lines = self._fs[path].splitlines()
            page = lines[offset : offset + limit]
            return {"content": "\n".join(page), "total_lines": len(lines)}
        return {"error": f"File not found in sandbox: {path}"}

    def write(self, path: str, content: str) -> dict:
        self._fs[path] = content
        return {"status": "ok", "path": path}

    def ls(self, path: str) -> dict:
        prefix = path.rstrip("/") + "/"
        children = sorted({k.split("/")[0] for k in self._fs if k.startswith(prefix)})
        return {"entries": children} if children else {"message": "No files found"}

    def delete(self, path: str) -> dict:
        if path in self._fs:
            del self._fs[path]
            return {"status": "ok"}
        return {"error": f"File not found: {path}"}


# =============================================================================
# --- Section 5: Streaming Output Handler -------------------------------------
# =============================================================================
# Deep Agents supports two streaming APIs:
#   v2 (raw): agent.stream(stream_mode, subgraphs=True, version="v2")
#   v3 (typed, recommended): agent.stream_events(version="v3")
#       -> .messages, .tool_calls, .values, .subagents, .output
#
# Streaming does NOT reduce billed tokens -- it changes time-to-first-token.
# Disconnect does NOT cancel the server worker; rejoin needs thread_id.


@dataclass
class StreamEvent:
    """A single event in the agent's output stream."""

    event_type: str       # "message_delta", "tool_call", "tool_result", "subagent", "progress"
    content: str
    metadata: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class StreamWriter:
    """Custom progress event writer for tools/nodes.

    In Deep Agents, get_stream_writer() inside a tool lets you emit
    custom progress events visible to the frontend.
    """

    def __init__(self):
        self._events: list[StreamEvent] = []
        self._listeners: list[Callable[[StreamEvent], None]] = []

    def on_event(self, callback: Callable[[StreamEvent], None]) -> None:
        """Register a listener for stream events."""
        self._listeners.append(callback)

    def write(self, event_type: str, content: str, **metadata) -> None:
        """Emit a stream event."""
        event = StreamEvent(event_type=event_type, content=content, metadata=metadata)
        self._events.append(event)
        for listener in self._listeners:
            listener(event)


class OutputStreamHandler:
    """Handles streaming from an agent execution.

    Interview point: Use stream.subagents for UI (one handle per task()
    delegation). Use stream.subgraphs only for debugging. Never consume
    raw subgraph namespaces in product UX.
    """

    def __init__(self):
        self.writer = StreamWriter()
        self._collected: list[StreamEvent] = []
        self.writer.on_event(self._collect)

    def _collect(self, event: StreamEvent) -> None:
        self._collected.append(event)

    def stream_tool_execution(
        self, tool_name: str, executor: Callable, args: dict
    ) -> dict:
        """Execute a tool while streaming progress events."""
        self.writer.write("tool_call", f"Calling {tool_name}", tool=tool_name, args=args)

        result = executor(args)

        self.writer.write(
            "tool_result",
            f"{tool_name} completed",
            tool=tool_name,
            exit_code=result.get("exit_code"),
        )
        return result

    def get_events(self) -> list[StreamEvent]:
        return list(self._collected)


# =============================================================================
# --- Section 6: Resource Limit Enforcement -----------------------------------
# =============================================================================
# Three execution surfaces with different resource profiles:
#
# | Surface          | Memory   | Timeout | Max Output  |
# |------------------|----------|---------|-------------|
# | Remote Sandbox   | ~2 GiB   | 3600s   | 100,000 B   |
# | LocalShell       | Host     | 120s    | 100,000 B   |
# | QuickJS Interp.  | 64 MiB   | 5.0s    | 4,000 chars |


@dataclass
class ResourceLimits:
    """Resource limits for a code execution environment.

    Interview numbers to memorize:
    - Interpreter: 64 MiB heap, 5.0s timeout, 4000 max_result_chars, 256 max_ptc_calls
    - LocalShell: 120s timeout, 100,000 max_output_bytes
    - Sandbox max_execute_timeout: 3600s (hard cap)
    """

    memory_limit_bytes: int = 64 * 1024 * 1024  # 64 MiB (interpreter default)
    timeout_seconds: float = 5.0                 # interpreter default
    max_output_chars: int = 4000                 # interpreter default
    max_ptc_calls: int = 256                     # parallel tool call limit


class ResourceLimitedExecutor:
    """Enforces resource limits on code execution.

    In production, resource limits are enforced by the sandbox provider
    (Firecracker cgroups, gVisor quotas). This simulates the pattern
    for interview discussion.
    """

    def __init__(self, limits: ResourceLimits):
        self.limits = limits

    def execute_with_limits(self, code: str) -> dict:
        """Execute code with memory, CPU, and time constraints.

        Three-tier sandbox isolation (from the module):
        Tier 3 -- MicroVMs (Firecracker): Own kernel. ~125ms boot, ~5MB overhead.
                  Powers AWS Lambda, E2B, Vercel.
        Tier 2 -- gVisor: Intercepts syscalls in userspace. Used by Google, Modal.
        Tier 1 -- Containers (Docker): Shared kernel. INSUFFICIENT for AI agents.
                  Microsoft May 2026 CVE: prompt injection -> host RCE via escape.
        """
        result = {"stdout": "", "stderr": "", "exit_code": 0,
                  "timed_out": False, "truncated": False, "memory_exceeded": False}

        try:
            # Capture output with a string buffer
            old_stdout = sys.stdout
            sys.stdout = buffer = io.StringIO()

            # Execute with timeout via threading
            exec_error = [None]

            def _run():
                try:
                    exec(code, {"__builtins__": __builtins__})
                except Exception as e:
                    exec_error[0] = e

            thread = threading.Thread(target=_run)
            thread.start()
            thread.join(timeout=self.limits.timeout_seconds)

            if thread.is_alive():
                result["timed_out"] = True
                result["stderr"] = f"Timeout after {self.limits.timeout_seconds}s"
                result["exit_code"] = -1
                # In production, the sandbox provider kills the process.
                # Threading cannot truly kill a Python thread.
            elif exec_error[0]:
                result["stderr"] = str(exec_error[0])
                result["exit_code"] = 1
            else:
                output = buffer.getvalue()
                # Enforce output size limit
                if len(output) > self.limits.max_output_chars:
                    output = output[: self.limits.max_output_chars]
                    result["truncated"] = True
                result["stdout"] = output

        finally:
            sys.stdout = old_stdout

        return result


# =============================================================================
# --- Section 7: Putting It All Together -- Production Wiring -----------------
# =============================================================================
# This demonstrates how a production agent wires the execution layer:
# CompositeBackend + permissions + sandbox + streaming.


def build_production_execution_layer() -> dict:
    """Wire a production-like execution environment.

    Architecture:
      /workspace/  -> SandboxBackend (remote Firecracker VM)
      /memories/   -> StateBackend (would be StoreBackend in prod)
      /shared/     -> FilesystemBackend (read-only shared resources)
      (default)    -> StateBackend (scratch, summaries, tool results)

    Interview walkthrough:
    1. Agent emits read_file(path="/workspace/src/main.py")
    2. FilesystemMiddleware intercepts
    3. Permission layer: first-match-wins check
    4. CompositeBackend: longest prefix match -> SandboxBackend
    5. Backend returns structured result (never raises)
    6. Results > 20,000 tokens offload to VFS (path + 10-line preview)
    """
    sandbox = SandboxBackend(
        idle_ttl_seconds=3600,
        memory_limit_mb=2048,
        cpu_vcpu=0.5,
    )

    backend = CompositeBackend(
        default=StateBackend(),
        routes={
            "/workspace/": sandbox,
            "/shared/": StateBackend(),  # Would be FilesystemBackend in prod
            "/memories/": StateBackend(),  # Would be StoreBackend in prod
        },
    )

    middleware = FilesystemMiddleware(
        backend=backend,
        permissions=PRODUCTION_PERMISSIONS,
    )

    stream_handler = OutputStreamHandler()

    return {
        "backend": backend,
        "middleware": middleware,
        "sandbox": sandbox,
        "stream_handler": stream_handler,
    }


# =============================================================================
# --- Section 8: RBAC per Role ------------------------------------------------
# =============================================================================
# Different roles get different tool sets and permission stacks.
# Enforce by constructing different permissions and excluded_tools per role.


ROLE_CONFIGS = {
    "analyst": {
        "tools": ["read_file", "glob", "grep"],
        "permissions": [
            FilesystemPermission(operations=["read"], paths=["/workspace/**", "/shared/**"],
                                 mode="allow"),
            FilesystemPermission(operations=["write"], paths=["/**"], mode="deny"),
            FilesystemPermission(operations=["read"], paths=["/**"], mode="deny"),
        ],
        "sandbox": False,
    },
    "engineer": {
        "tools": ["read_file", "write_file", "edit_file", "glob", "grep"],
        "permissions": [
            FilesystemPermission(operations=["read", "write"],
                                 paths=["/**/.env", "/**/*.key"], mode="deny"),
            FilesystemPermission(operations=["read", "write"],
                                 paths=["/workspace/**"], mode="allow"),
            FilesystemPermission(operations=["read"], paths=["/shared/**"], mode="allow"),
            FilesystemPermission(operations=["read", "write"], paths=["/**"], mode="deny"),
        ],
        "sandbox": False,
    },
    "admin": {
        "tools": ["read_file", "write_file", "edit_file", "delete", "glob", "grep",
                  "execute"],
        "permissions": PRODUCTION_PERMISSIONS,
        "sandbox": True,
    },
}


# =============================================================================
# --- Demo / Self-Test --------------------------------------------------------
# =============================================================================


def demo():
    """Demonstrate the execution environment components."""
    print("=" * 60)
    print("DEMO: Deep Agents Execution Environment")
    print("=" * 60)

    # 1. VFS with CompositeBackend
    print("\n--- CompositeBackend Routing ---")
    env = build_production_execution_layer()
    mw = env["middleware"]

    # Write a file to workspace (allowed)
    result = mw.handle_tool_call("write_file", {
        "path": "/workspace/hello.py",
        "content": "print('hello world')",
    })
    print(f"Write /workspace/hello.py: {result}")

    # Read it back (allowed)
    result = mw.handle_tool_call("read_file", {"path": "/workspace/hello.py"})
    print(f"Read /workspace/hello.py: {result}")

    # Try to read a secret file (denied by first permission rule)
    result = mw.handle_tool_call("read_file", {"path": "/workspace/.env"})
    print(f"Read /workspace/.env: {result}")

    # Try to write to /shared/ (denied -- read-only)
    result = mw.handle_tool_call("write_file", {
        "path": "/shared/data.csv",
        "content": "a,b,c",
    })
    print(f"Write /shared/data.csv: {result}")

    # 2. Permission evaluation
    print("\n--- Permission Evaluation ---")
    for path in ["/workspace/main.py", "/workspace/.env", "/shared/doc.md", "/etc/passwd"]:
        read_result = check_permission(PRODUCTION_PERMISSIONS, "read", path)
        write_result = check_permission(PRODUCTION_PERMISSIONS, "write", path)
        print(f"  {path:30s}  read={read_result:10s}  write={write_result}")

    # 3. Sandbox execution with timeout
    print("\n--- Sandbox Execution ---")
    sandbox = env["sandbox"]
    result = sandbox.execute("echo 'Hello from sandbox' && date", timeout=10)
    print(f"Command result: exit_code={result['exit_code']}, "
          f"stdout={result['stdout'].strip()!r}")

    # 4. Resource limits
    print("\n--- Resource-Limited Execution ---")
    executor = ResourceLimitedExecutor(ResourceLimits(timeout_seconds=2.0))
    result = executor.execute_with_limits("print('2 + 2 =', 2 + 2)")
    print(f"Quick calc: {result['stdout'].strip()}")

    # 5. Streaming
    print("\n--- Streaming Events ---")
    handler = env["stream_handler"]
    handler.stream_tool_execution(
        "execute",
        lambda args: sandbox.execute(args["command"], timeout=5),
        {"command": "echo 'streamed output'"},
    )
    for event in handler.get_events():
        print(f"  [{event.event_type}] {event.content}")

    print("\n" + "=" * 60)
    print("Key interview numbers:")
    print("  8 built-in FS tools (ls, read_file, write_file, edit_file,")
    print("    delete, glob, grep, execute)")
    print("  20,000 tokens: offload threshold (10-line preview)")
    print("  3,600s: max execute timeout")
    print("  64 MiB / 5.0s / 4,000 chars / 256 PTC: interpreter limits")
    print("  fail-open: no matching permission rule = allow")
    print("=" * 60)


if __name__ == "__main__":
    demo()
