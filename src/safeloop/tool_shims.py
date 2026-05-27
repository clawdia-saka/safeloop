"""Run-local PATH shims for routing tool calls through SafeLoop."""
from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SHIMMED_TOOLS = (
    "rm",
    "mv",
    "cp",
    "mkdir",
    "rmdir",
    "touch",
    "chmod",
    "chown",
    "curl",
    "wget",
    "gh",
    "git",
    "python",
    "python3",
    "node",
    "npm",
    "npx",
    "pnpm",
    "yarn",
    "bun",
    "sh",
    "bash",
    "zsh",
)
SHIM_SCHEMA_VERSION = "tool-shims.v2"
SHIM_COVERAGE_VERSION = "v2"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _atomic_json(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _sha_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _shim_script(tool: str, python_executable: str) -> str:
    return f"""#!{python_executable}
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

TOOL = {tool!r}


def _first_non_option(args):
    for arg in args:
        if arg == "--":
            continue
        if arg.startswith("-"):
            continue
        return arg
    return None


def _first_url(args):
    for arg in args:
        if "://" in arg or arg.startswith("mailto:"):
            return arg
    return None


def _option_value_flags(tool):
    if tool == "git":
        return {{"-C", "-c", "--git-dir", "--work-tree"}}
    if tool == "curl":
        return {{"-X", "--request", "-d", "--data", "--data-raw", "-H", "--header"}}
    return set()


def _first_path_arg(tool, args, *, start=0):
    skip_next = False
    value_flags = _option_value_flags(tool)
    for arg in args[start:]:
        if skip_next:
            skip_next = False
            continue
        if arg == "--":
            continue
        if arg in value_flags:
            skip_next = True
            continue
        if arg.startswith("-"):
            continue
        return arg
    return None


def _subcommand_arg(tool, args):
    skip_next = False
    value_flags = _option_value_flags(tool)
    for index, arg in enumerate(args):
        if skip_next:
            skip_next = False
            continue
        if arg in value_flags:
            skip_next = True
            continue
        if arg == "--" or arg.startswith("-"):
            continue
        return index, arg
    return None, None


def _infer(tool, args):
    target = _first_non_option(args) or f"{{tool}} request"
    target_kind = "auto"
    local_mutation_actions = {{
        "rm": "delete",
        "mv": "move",
        "cp": "copy",
        "mkdir": "mkdir",
        "rmdir": "rmdir",
        "touch": "touch",
        "chmod": "chmod",
        "chown": "chown",
    }}
    if tool in local_mutation_actions:
        return local_mutation_actions[tool], target, target_kind
    if tool in {{"curl", "wget"}}:
        url = _first_url(args) or target
        upper_args = {{arg.upper() for arg in args}}
        action = "post" if tool == "curl" and ("POST" in upper_args or any(arg.startswith("-d") for arg in args)) else "send"
        return action, url, "external" if "://" in url or url.startswith("mailto:") else "unknown"
    if tool == "gh":
        action = "github " + " ".join(args[:2]) if args else "github"
        return action, "gh " + " ".join(args), "external"
    if tool == "git":
        action_index, action = _subcommand_arg(tool, args)
        action = action or "git"
        if action in {{"status", "log", "diff", "show"}}:
            return action, _first_path_arg(tool, args, start=(action_index or 0) + 1) or ".", "auto"
        if action in {{"push", "pull", "fetch", "clone"}}:
            return action, "git " + " ".join(args), "external"
        return action, "git " + " ".join(args), "unknown"
    if tool in {{"python", "python3"}}:
        if args[:1] in (["--version"], ["-V"]):
            return "version", f"{{tool}} version", "auto"
        if args[:1] in (["--help"], ["-h"]):
            return "help", f"{{tool}} help", "auto"
        return "execute", target, "unknown"
    if tool in {{"sh", "bash", "zsh"}}:
        if args[:1] == ["-n"]:
            return "syntax_check", _first_path_arg(tool, args, start=1) or f"{{tool}} syntax check", "auto"
        return "execute", target, "unknown"
    if tool in {{"node", "npm", "npx", "pnpm", "yarn", "bun"}}:
        if args[:1] in (["--version"], ["-v"]):
            return "version", f"{{tool}} version", "auto"
        if args[:1] in (["--help"], ["-h"]):
            return "help", f"{{tool}} help", "auto"
        return "execute", target, "unknown"
    return tool, target, "unknown"


def _load_output(path):
    try:
        return Path(path).read_bytes()
    except OSError:
        return b""


def main():
    args = sys.argv[1:]
    run_dir = os.environ.get("SAFELOOP_RUN_DIR")
    workspace_root = os.environ.get("SAFELOOP_TOOL_SHIM_WORKSPACE_ROOT") or os.environ.get("PWD") or "."
    safeloop_python = os.environ.get("SAFELOOP_TOOL_SHIM_PYTHON") or sys.executable
    policy_profile = os.environ.get("SAFELOOP_POLICY_PROFILE") or "strict-local"
    if not run_dir:
        print(f"SafeLoop tool shim {{TOOL}} blocked: SAFELOOP_RUN_DIR is not set", file=sys.stderr)
        return 127
    action, target, target_kind = _infer(TOOL, args)
    original_path = os.environ.get("SAFELOOP_TOOL_SHIM_ORIGINAL_PATH") or os.environ.get("PATH") or ""
    real_executable = shutil.which(TOOL, path=original_path) or TOOL
    command = [
        safeloop_python,
        "-m",
        "safeloop.cli",
        "firewall",
        "exec",
        run_dir,
        "--tool",
        TOOL,
        "--action",
        action,
        "--target",
        target,
        "--workspace-root",
        workspace_root,
        "--target-kind",
        target_kind,
        "--reason",
        f"tool shim intercepted {{TOOL}}",
        "--actor",
        "tool-shim",
        "--policy-profile",
        policy_profile,
        "--json",
        "--",
        real_executable,
        *args,
    ]
    env = os.environ.copy()
    shim_pythonpath = env.get("SAFELOOP_TOOL_SHIM_PYTHONPATH")
    if shim_pythonpath:
        existing = env.get("PYTHONPATH")
        env["PYTHONPATH"] = shim_pythonpath if not existing else shim_pythonpath + os.pathsep + existing
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, check=False)
    payload = None
    if result.stdout.strip():
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            payload = None
    if isinstance(payload, dict):
        exec_event = payload.get("exec_event") if isinstance(payload.get("exec_event"), dict) else {{}}
        if payload.get("executed") is True:
            stdout = _load_output(Path(run_dir) / str(exec_event.get("stdout_path", "")))
            stderr = _load_output(Path(run_dir) / str(exec_event.get("stderr_path", "")))
            if stdout:
                sys.stdout.buffer.write(stdout)
            if stderr:
                sys.stderr.buffer.write(stderr)
            return int(payload.get("exit_code") or 0)
        reason = exec_event.get("block_reason") or payload.get("status") or "blocked"
        print(f"SafeLoop tool shim blocked {{TOOL}}: {{reason}}", file=sys.stderr)
        return 1
    if result.stderr:
        sys.stderr.write(result.stderr)
    if result.stdout:
        sys.stdout.write(result.stdout)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
"""


def create_tool_shims(
    run_dir: str | Path,
    *,
    workspace_root: str | Path,
    original_path: str,
    python_executable: str | None = None,
    policy_profile: str = "strict-local",
) -> dict[str, Any]:
    """Create run-local PATH shims and return metadata for run.json."""

    run_path = Path(run_dir)
    shim_root = run_path / "tool-shims"
    bin_dir = shim_root / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    python_value = python_executable or sys.executable
    for tool in SHIMMED_TOOLS:
        path = bin_dir / tool
        path.write_text(_shim_script(tool, python_value), encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    metadata = {
        "schema_version": SHIM_SCHEMA_VERSION,
        "enabled": True,
        "created_at": _utc_now(),
        "coverage_version": SHIM_COVERAGE_VERSION,
        "bin_dir": str(bin_dir),
        "workspace_root": str(Path(workspace_root).resolve()),
        "policy_profile": policy_profile,
        "tools": list(SHIMMED_TOOLS),
        "original_path_sha256": _sha_text(original_path),
        "original_path_entry_count": len([part for part in original_path.split(os.pathsep) if part]),
        "bypass_caveat": "PATH shims intercept command-name lookups only; absolute executable paths and already-running processes can bypass them.",
    }
    _atomic_json(shim_root / "tool-shims.json", metadata)
    return metadata
