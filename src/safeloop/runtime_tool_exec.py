"""Guarded runtime tool execution through the SafeLoop firewall."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from safeloop.runtime_tool_firewall import FIREWALL_LOG, firewall_preflight
from safeloop.storage import exclusive_lock

EXEC_LOG = "runtime-tool-exec.jsonl"
EXEC_OUTPUT_DIR = "runtime-tool-exec"
SCHEMA_VERSION = "runtime-tool-exec.v1"
RESULT_SCHEMA_VERSION = "runtime-tool-exec-result.v1"
ALLOWED_READ_ONLY_EXECUTABLES = {
    "cat",
    "diff",
    "grep",
    "head",
    "ls",
    "pwd",
    "rg",
    "stat",
    "tail",
    "wc",
}

_SENSITIVE_RE = re.compile(r"(?i)(authorization|bearer\s+[a-z0-9._~+/=-]+|api[_-]?key|secret|token|password)[:= ]")


class RuntimeToolExecError(ValueError):
    """Raised when a guarded tool request cannot be evaluated safely."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sha_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _sha_event(record: dict[str, Any]) -> str:
    payload = dict(record)
    payload.pop("event_hash", None)
    data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _sha_bytes(data)


def _is_sha(value: Any) -> bool:
    return isinstance(value, str) and value.startswith("sha256:") and len(value) == 71


def _load_run_id(run_path: Path) -> str:
    try:
        data = json.loads((run_path / "run.json").read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeToolExecError(f"missing run.json in run directory: {run_path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeToolExecError(f"invalid run.json in run directory: {run_path}") from exc
    run_id = str(data.get("run_id") or "").strip()
    if not run_id:
        raise RuntimeToolExecError("run.json must include run_id")
    return run_id


def _require_safe_command(command: Sequence[str]) -> list[str]:
    values = [str(part) for part in command]
    if not values:
        raise RuntimeToolExecError("command is required")
    if any(not value for value in values):
        raise RuntimeToolExecError("command arguments must be non-empty strings")
    if any(_SENSITIVE_RE.search(value) for value in values):
        raise RuntimeToolExecError("raw sensitive payload storage is not allowed; store narrow references only")
    return values


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError:
        return False
    return True


def _safe_workspace_dir(workspace_root: str | Path, cwd: str | Path | None) -> tuple[Path, Path]:
    root = Path(workspace_root).resolve()
    if not root.exists() or not root.is_dir():
        raise RuntimeToolExecError(f"workspace_root must be an existing directory: {root}")
    raw_cwd = Path(cwd) if cwd is not None else root
    effective_cwd = raw_cwd if raw_cwd.is_absolute() else root / raw_cwd
    effective_cwd = effective_cwd.resolve()
    if not effective_cwd.exists() or not effective_cwd.is_dir():
        raise RuntimeToolExecError(f"cwd must be an existing directory: {effective_cwd}")
    if not _is_relative_to(effective_cwd, root):
        raise RuntimeToolExecError("cwd must stay inside workspace_root")
    return root, effective_cwd


def _validate_read_target(target: str, workspace_root: Path) -> str | None:
    if any(char in target for char in ["*", "?", "["]):
        return "target globs are not allowed for guarded execution"
    candidate = Path(target)
    path = candidate if candidate.is_absolute() else workspace_root / candidate
    try:
        resolved = path.resolve(strict=False)
    except OSError:
        return "target path cannot be resolved safely"
    if not _is_relative_to(resolved, workspace_root):
        return "target must stay inside workspace_root"
    return None


def _validate_command_paths(command: Sequence[str], *, workspace_root: Path, cwd: Path) -> str | None:
    for raw in command[1:]:
        if raw == "--":
            return "nested command separators are not allowed"
        if ".." in Path(raw).parts:
            return "command path traversal is not allowed"
        path_like = Path(raw).is_absolute() or "/" in raw or raw in {".", ".."}
        if not path_like and (cwd / raw).exists():
            path_like = True
        if not path_like:
            continue
        path = Path(raw)
        candidate = path if path.is_absolute() else cwd / path
        if not _is_relative_to(candidate, workspace_root):
            return "command path arguments must stay inside workspace_root"
    return None


def _command_block_reason(tool: str, command: Sequence[str], *, workspace_root: Path, cwd: Path, target: str) -> str | None:
    executable = Path(command[0]).name
    tool_value = tool.strip()
    if tool_value not in ALLOWED_READ_ONLY_EXECUTABLES:
        return f"tool {tool_value!r} is not in the read-only execution allowlist"
    if executable != tool_value:
        return f"command executable {executable!r} does not match tool {tool_value!r}"
    target_reason = _validate_read_target(target, workspace_root)
    if target_reason:
        return target_reason
    return _validate_command_paths(command, workspace_root=workspace_root, cwd=cwd)


def _safe_env() -> dict[str, str]:
    allowed = {"HOME", "LANG", "LC_ALL", "LC_CTYPE", "PATH", "PYTHONPATH", "SAFELOOP_RUN_DIR", "SAFELOOP_RUN_ID", "SAFELOOP_ACTION_ID"}
    return {key: value for key, value in os.environ.items() if key in allowed}


def _read_exec_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    previous_hash: str | None = None
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeToolExecError(f"invalid {EXEC_LOG} line {line_no}") from exc
            if not isinstance(item, dict):
                raise RuntimeToolExecError(f"{EXEC_LOG} line {line_no}: record must be an object")
            event_hash = item.get("event_hash")
            if item.get("prev_event_hash") != previous_hash:
                raise RuntimeToolExecError(f"{EXEC_LOG} line {line_no}: prev_event_hash mismatch")
            if not _is_sha(event_hash):
                raise RuntimeToolExecError(f"{EXEC_LOG} line {line_no}: malformed event_hash")
            if _sha_event(item) != event_hash:
                raise RuntimeToolExecError(f"{EXEC_LOG} line {line_no}: event_hash mismatch")
            previous_hash = event_hash
            events.append(item)
    return events


def _append_exec_event(run_path: Path, record: dict[str, Any]) -> dict[str, Any]:
    path = run_path / EXEC_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+", encoding="utf-8") as lock_handle:
        with exclusive_lock(lock_handle):
            events = _read_exec_events(path)
            record = dict(record)
            record["prev_event_hash"] = events[-1]["event_hash"] if events else None
            record["event_hash"] = _sha_event(record)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True) + "\n")
            return record


def _write_stream(run_path: Path, exec_id: str, name: str, data: bytes) -> dict[str, Any]:
    rel = f"{EXEC_OUTPUT_DIR}/{exec_id}/{name}.txt"
    path = run_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {
        f"{name}_path": rel,
        f"{name}_sha256": _sha_bytes(data),
        f"{name}_bytes": len(data),
    }


def _base_exec_record(
    *,
    run_id: str,
    exec_id: str,
    firewall_event: dict[str, Any],
    tool: str,
    action: str,
    target: str,
    actor: str,
    reason: str,
    command: Sequence[str],
    workspace_root: Path,
    cwd: Path,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "timestamp": _utc_now(),
        "run_id": run_id,
        "exec_id": exec_id,
        "firewall_event_id": firewall_event.get("event_id"),
        "firewall_route": firewall_event.get("route"),
        "tool": tool,
        "action": action,
        "target": target,
        "actor": actor or "unknown",
        "reason": reason,
        "command": list(command),
        "workspace_root": str(workspace_root),
        "cwd": str(cwd),
        "artifacts": [EXEC_LOG, FIREWALL_LOG],
    }


def execute_tool_request(
    run_dir: str | Path,
    *,
    tool: str,
    action: str,
    target: str,
    command: Sequence[str],
    workspace_root: str | Path = ".",
    cwd: str | Path | None = None,
    reason: str,
    actor: str = "unknown",
    target_kind: str = "auto",
    timeout_seconds: float = 30,
    action_id: str | None = None,
) -> dict[str, Any]:
    """Preflight a tool request and execute only allowlisted read-only commands."""

    run_path = Path(run_dir)
    command_values = _require_safe_command(command)
    root, effective_cwd = _safe_workspace_dir(workspace_root, cwd)
    timeout_value = float(timeout_seconds)
    if timeout_value <= 0:
        raise RuntimeToolExecError("timeout_seconds must be greater than zero")

    firewall_event = firewall_preflight(
        run_dir=run_path,
        tool=tool,
        action=action,
        target=target,
        workspace_root=root,
        reason=reason,
        actor=actor,
        target_kind=target_kind,  # type: ignore[arg-type]
        action_id=action_id,
        source="exec_wrapper",
    )
    run_id = _load_run_id(run_path)
    exec_id = "texec-" + uuid.uuid4().hex
    record = _base_exec_record(
        run_id=run_id,
        exec_id=exec_id,
        firewall_event=firewall_event,
        tool=tool,
        action=action,
        target=target,
        actor=actor,
        reason=reason,
        command=command_values,
        workspace_root=root,
        cwd=effective_cwd,
    )

    route = str(firewall_event.get("route") or "")
    if route != "allow_read_only":
        record.update(
            {
                "status": "blocked",
                "executed": False,
                "exit_code": None,
                "block_reason": f"firewall route {route} does not permit execution",
            }
        )
        exec_event = _append_exec_event(run_path, record)
        return {"schema_version": RESULT_SCHEMA_VERSION, "status": "blocked", "executed": False, "firewall_event": firewall_event, "exec_event": exec_event}

    block_reason = _command_block_reason(tool, command_values, workspace_root=root, cwd=effective_cwd, target=target)
    if block_reason:
        record.update({"status": "blocked", "executed": False, "exit_code": None, "block_reason": block_reason})
        exec_event = _append_exec_event(run_path, record)
        return {"schema_version": RESULT_SCHEMA_VERSION, "status": "blocked", "executed": False, "firewall_event": firewall_event, "exec_event": exec_event}

    started = time.monotonic()
    try:
        completed = subprocess.run(
            command_values,
            cwd=effective_cwd,
            env=_safe_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_value,
            check=False,
        )
        duration_ms = int((time.monotonic() - started) * 1000)
        record.update(
            {
                "status": "executed",
                "executed": True,
                "exit_code": completed.returncode,
                "timed_out": False,
                "duration_ms": duration_ms,
            }
        )
        record.update(_write_stream(run_path, exec_id, "stdout", completed.stdout))
        record.update(_write_stream(run_path, exec_id, "stderr", completed.stderr))
        exec_event = _append_exec_event(run_path, record)
        return {"schema_version": RESULT_SCHEMA_VERSION, "status": "executed", "executed": True, "exit_code": completed.returncode, "firewall_event": firewall_event, "exec_event": exec_event}
    except FileNotFoundError as exc:
        record.update({"status": "execution_error", "executed": False, "exit_code": 127, "error": str(exc)})
        exec_event = _append_exec_event(run_path, record)
        return {"schema_version": RESULT_SCHEMA_VERSION, "status": "execution_error", "executed": False, "exit_code": 127, "firewall_event": firewall_event, "exec_event": exec_event}
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or b""
        stderr = exc.stderr or b""
        if isinstance(stdout, str):
            stdout = stdout.encode()
        if isinstance(stderr, str):
            stderr = stderr.encode()
        record.update({"status": "timed_out", "executed": True, "exit_code": 124, "timed_out": True, "duration_ms": int(timeout_value * 1000)})
        record.update(_write_stream(run_path, exec_id, "stdout", stdout))
        record.update(_write_stream(run_path, exec_id, "stderr", stderr))
        exec_event = _append_exec_event(run_path, record)
        return {"schema_version": RESULT_SCHEMA_VERSION, "status": "timed_out", "executed": True, "exit_code": 124, "firewall_event": firewall_event, "exec_event": exec_event}


def read_runtime_tool_exec_events(run_dir: str | Path) -> list[dict[str, Any]]:
    """Read runtime-tool-exec.jsonl events and verify the hash chain."""

    path = Path(run_dir) / EXEC_LOG
    if not path.exists():
        return []
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+", encoding="utf-8") as lock_handle:
        with exclusive_lock(lock_handle):
            return _read_exec_events(path)
