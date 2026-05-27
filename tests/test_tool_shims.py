from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from safeloop.external_outbox import read_external_outbox
from safeloop.runtime_tool_firewall import read_runtime_tool_firewall_events
from safeloop.tool_shims import create_tool_shims

ROOT = Path(__file__).resolve().parents[1]


def _make_run_dir(tmp_path: Path) -> Path:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "run.json").write_text(
        json.dumps(
            {
                "run_id": "run-tool-shims",
                "task_id": "tool-shims-v2",
                "status": "running",
                "started_at": "2026-05-27T00:00:00+00:00",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return run_dir


def _install_shims(tmp_path: Path, *, policy_profile: str = "strict-local") -> tuple[Path, Path, Path, dict[str, str]]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    run_dir = _make_run_dir(tmp_path)
    original_path = os.environ.get("PATH", "")
    metadata = create_tool_shims(
        run_dir,
        workspace_root=workspace,
        original_path=original_path,
        python_executable=sys.executable,
        policy_profile=policy_profile,
    )
    env = {
        **os.environ,
        "SAFELOOP_RUN_DIR": str(run_dir),
        "SAFELOOP_RUN_ID": "run-tool-shims",
        "SAFELOOP_POLICY_PROFILE": policy_profile,
        "SAFELOOP_TOOL_SHIM_WORKSPACE_ROOT": str(workspace),
        "SAFELOOP_TOOL_SHIM_ORIGINAL_PATH": original_path,
        "SAFELOOP_TOOL_SHIM_PYTHON": sys.executable,
        "SAFELOOP_TOOL_SHIM_PYTHONPATH": str(ROOT / "src"),
    }
    return run_dir, workspace, Path(metadata["bin_dir"]), env


def _run_shim(bin_dir: Path, tool: str, args: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(bin_dir / tool), *args],
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=20,
    )


def test_git_read_only_shims_classify_status_log_and_diff_as_read_only(tmp_path: Path) -> None:
    run_dir, workspace, bin_dir, env = _install_shims(tmp_path)
    metadata = json.loads((run_dir / "tool-shims" / "tool-shims.json").read_text(encoding="utf-8"))
    assert metadata["schema_version"] == "tool-shims.v2"
    assert metadata["coverage_version"] == "v2"
    assert {"git", "python3", "bash", "node"} <= set(metadata["tools"])

    for args in [["status"], ["-C", ".", "status"], ["log", "--oneline"], ["diff", "--cached"]]:
        _run_shim(bin_dir, "git", args, cwd=workspace, env=env)

    events = read_runtime_tool_firewall_events(run_dir)
    assert [event["route"] for event in events] == ["allow_read_only", "allow_read_only", "allow_read_only", "allow_read_only"]
    assert [event["tool"] for event in events] == ["git", "git", "git", "git"]
    assert [event["action"] for event in events] == ["status", "status", "log", "diff"]
    assert [event["target_kind"] for event in events] == ["auto", "auto", "auto", "auto"]
    assert all(event["manual_review_required"] is False for event in events)


def test_git_push_shim_routes_to_external_outbox(tmp_path: Path) -> None:
    run_dir, workspace, bin_dir, env = _install_shims(tmp_path)

    _run_shim(bin_dir, "git", ["push", "origin", "main"], cwd=workspace, env=env)

    event = read_runtime_tool_firewall_events(run_dir)[0]
    assert event["route"] == "external_outbox"
    assert event["target_kind"] == "external"
    assert event["target"] == "git push origin main"
    outbox = read_external_outbox(run_dir)
    assert outbox["counts"]["pending"] == 1
    assert outbox["items"][0]["action"] == "push"


def test_ci_readonly_profile_turns_git_push_shim_into_manual_review(tmp_path: Path) -> None:
    run_dir, workspace, bin_dir, env = _install_shims(tmp_path, policy_profile="ci-readonly")

    _run_shim(bin_dir, "git", ["push", "origin", "main"], cwd=workspace, env=env)

    event = read_runtime_tool_firewall_events(run_dir)[0]
    assert event["route"] == "manual_review"
    assert event["policy_profile"] == "ci-readonly"
    assert event["target_kind"] == "external"
    assert not (run_dir / "external-outbox.json").exists()


def test_python_and_sh_script_shims_remain_manual_review(tmp_path: Path) -> None:
    run_dir, workspace, bin_dir, env = _install_shims(tmp_path)
    (workspace / "script.py").write_text("from pathlib import Path\nPath('python-ran').write_text('bad')\n", encoding="utf-8")
    (workspace / "script.sh").write_text("touch sh-ran\n", encoding="utf-8")

    _run_shim(bin_dir, "python", ["script.py"], cwd=workspace, env=env)
    _run_shim(bin_dir, "sh", ["script.sh"], cwd=workspace, env=env)

    events = read_runtime_tool_firewall_events(run_dir)
    assert [event["route"] for event in events] == ["manual_review", "manual_review"]
    assert [event["tool"] for event in events] == ["python", "sh"]
    assert [event["target"] for event in events] == ["script.py", "script.sh"]
    assert all(event["manual_review_required"] is True for event in events)
    assert not (workspace / "python-ran").exists()
    assert not (workspace / "sh-ran").exists()
