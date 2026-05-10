from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "safeloop.cli", *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def run_dir_from(stdout: str) -> Path:
    return Path([line.split(":", 1)[1].strip() for line in stdout.splitlines() if line.startswith("Run dir:")][0])


def test_watch_loop_cli_alias_uses_monotonic_checkpoint_ids(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "agent.py").write_text(
        "from pathlib import Path\nimport time\n"
        "Path('a.txt').write_text('1')\n"
        "time.sleep(0.18)\n"
        "Path('b.txt').write_text('2')\n",
        encoding="utf-8",
    )

    result = run_cli("watch", "--loop", "--task-id", "loop", "--repo", str(repo), "--run-root", str(tmp_path / "runs"), "--debounce-ms", "10", "--", sys.executable, "agent.py")

    assert result.returncode == 0, result.stderr
    run_dir = run_dir_from(result.stdout)
    checkpoints = sorted((run_dir / "checkpoints").glob("cp-*"))
    assert [cp.name for cp in checkpoints] == ["cp-0001", "cp-0002"]
    assert [read_json(cp / "checkpoint.json")["checkpoint_seq"] for cp in checkpoints] == [1, 2]
    assert [read_json(cp / "checkpoint.json")["allocated_by"] for cp in checkpoints] == ["monotonic-repo-task", "monotonic-repo-task"]


def test_checkpoint_ids_are_monotonic_across_runs_for_same_repo_task(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    run_root = tmp_path / "runs"
    agent = repo / "agent.py"

    agent.write_text("from pathlib import Path\nPath('one.txt').write_text('1')\n", encoding="utf-8")
    first = run_cli("watch-run", "--task-id", "reuse", "--repo", str(repo), "--run-root", str(run_root), "--debounce-ms", "10", "--", sys.executable, "agent.py")
    assert first.returncode == 0, first.stderr

    agent.write_text("from pathlib import Path\nPath('two.txt').write_text('2')\n", encoding="utf-8")
    second = run_cli("watch-run", "--task-id", "reuse", "--repo", str(repo), "--run-root", str(run_root), "--debounce-ms", "10", "--", sys.executable, "agent.py")
    assert second.returncode == 0, second.stderr

    first_cp = run_dir_from(first.stdout) / "checkpoints" / "cp-0001" / "checkpoint.json"
    second_run_dir = run_dir_from(second.stdout)
    second_checkpoints = sorted((second_run_dir / "checkpoints").glob("cp-*"))
    assert [cp.name for cp in second_checkpoints] == ["cp-0002"]
    assert read_json(first_cp)["allocated_by"] == "monotonic-repo-task"
    assert read_json(second_checkpoints[0] / "checkpoint.json")["checkpoint_seq"] == 2
    assert read_json(second_checkpoints[0] / "checkpoint.json")["monotonic_scope"] == "repo_task_cross_run"


def test_checkpoint_artifact_hash_binding_is_recorded_in_checkpoint(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "agent.py").write_text("open('result.txt','w').write('v1')\n", encoding="utf-8")

    result = run_cli("watch-run", "--task-id", "bind", "--repo", str(repo), "--run-root", str(tmp_path / "runs"), "--", sys.executable, "agent.py")

    assert result.returncode == 0, result.stderr
    cp = run_dir_from(result.stdout) / "checkpoints" / "cp-0001"
    checkpoint = read_json(cp / "checkpoint.json")
    bound = checkpoint["artifact_digests"]
    assert set(bound) == {"manifest.json", "diff.patch", "restore-manifest.json", "summary.md"}
    for name, digest in bound.items():
        assert digest.startswith("sha256:"), name


def test_verify_reports_malformed_bound_artifact_hash_explicitly(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "agent.py").write_text("open('result.txt','w').write('v1')\n", encoding="utf-8")
    result = run_cli("watch-run", "--task-id", "malformed-digest", "--repo", str(repo), "--run-root", str(tmp_path / "runs"), "--", sys.executable, "agent.py")
    assert result.returncode == 0, result.stderr
    run_dir = run_dir_from(result.stdout)
    timeline = run_dir / "timeline.jsonl"
    events = [json.loads(line) for line in timeline.read_text(encoding="utf-8").splitlines() if line.strip()]
    checkpoint_event = next(event for event in events if event["type"] == "checkpoint_created")
    checkpoint_event["payload"]["artifact_digests"]["diff.patch"] = "sha256:not-hex"
    timeline.write_text("\n".join(json.dumps(event, sort_keys=True) for event in events) + "\n", encoding="utf-8")

    verify = run_cli("verify-artifacts", str(run_dir))

    assert verify.returncode == 1
    payload = read_json(run_dir / "verification" / "verify-artifacts-result.json")
    assert payload["status"] == "invalid"
    assert "malformed-artifact-hash diff.patch" in payload["issues"]


def test_verify_reports_missing_required_checkpoint_artifact_binding(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "agent.py").write_text("open('result.txt','w').write('v1')\n", encoding="utf-8")
    result = run_cli(
        "watch-run",
        "--task-id",
        "missing-required-binding",
        "--repo",
        str(repo),
        "--run-root",
        str(tmp_path / "runs"),
        "--",
        sys.executable,
        "agent.py",
    )
    assert result.returncode == 0, result.stderr
    run_dir = run_dir_from(result.stdout)
    timeline = run_dir / "timeline.jsonl"
    events = [json.loads(line) for line in timeline.read_text(encoding="utf-8").splitlines() if line.strip()]
    checkpoint_event = next(event for event in events if event["type"] == "checkpoint_created")
    checkpoint_event["payload"]["artifact_digests"].pop("summary.md")
    timeline.write_text("\n".join(json.dumps(event, sort_keys=True) for event in events) + "\n", encoding="utf-8")

    verify = run_cli("verify-artifacts", str(run_dir))

    assert verify.returncode == 1
    payload = read_json(run_dir / "verification" / "verify-artifacts-result.json")
    assert payload["status"] == "invalid"
    assert "missing required checkpoint artifact binding cp-0001/summary.md" in payload["issues"]


def test_checkpoint_manifest_declares_required_artifacts(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "agent.py").write_text("open('result.txt','w').write('v1')\n", encoding="utf-8")

    result = run_cli(
        "watch-run",
        "--task-id",
        "manifest-required",
        "--repo",
        str(repo),
        "--run-root",
        str(tmp_path / "runs"),
        "--",
        sys.executable,
        "agent.py",
    )

    assert result.returncode == 0, result.stderr
    cp = run_dir_from(result.stdout) / "checkpoints" / "cp-0001"
    manifest = read_json(cp / "manifest.json")
    assert manifest["required_artifacts"] == [
        "checkpoint.json",
        "diff.patch",
        "manifest.json",
        "restore-manifest.json",
        "summary.md",
    ]


def test_verify_reports_manifest_missing_required_artifact(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "agent.py").write_text("open('result.txt','w').write('v1')\n", encoding="utf-8")
    result = run_cli(
        "watch-run",
        "--task-id",
        "manifest-required-tamper",
        "--repo",
        str(repo),
        "--run-root",
        str(tmp_path / "runs"),
        "--",
        sys.executable,
        "agent.py",
    )
    assert result.returncode == 0, result.stderr
    run_dir = run_dir_from(result.stdout)
    manifest_path = run_dir / "checkpoints" / "cp-0001" / "manifest.json"
    manifest = read_json(manifest_path)
    manifest["required_artifacts"].remove("summary.md")
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    verify = run_cli("verify-artifacts", str(run_dir))

    assert verify.returncode == 1
    payload = read_json(run_dir / "verification" / "verify-artifacts-result.json")
    assert payload["status"] == "invalid"
    assert "manifest missing required_artifact cp-0001/summary.md" in payload["issues"]


def test_undo_preflight_digest_verifies_restore_manifest_before_dry_run(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "agent.py").write_text("open('result.txt','w').write('v1')\n", encoding="utf-8")
    result = run_cli("watch-run", "--task-id", "undo-digest", "--repo", str(repo), "--run-root", str(tmp_path / "runs"), "--", sys.executable, "agent.py")
    run_dir = run_dir_from(result.stdout)
    run_id = read_json(run_dir / "run.json")["run_id"]
    restore = run_dir / "checkpoints" / "cp-0001" / "restore-manifest.json"
    payload = read_json(restore)
    payload["created_files"].append("evil.txt")
    restore.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    dry = run_cli("undo", str(run_dir), run_id, "cp-0001", "--dry-run")

    assert dry.returncode != 0
    assert "artifact verification" in dry.stderr or "digest mismatch" in dry.stderr


def test_verify_detects_incomplete_capture_and_invalid_partial_finalization(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "agent.py").write_text("open('result.txt','w').write('v1')\n", encoding="utf-8")
    result = run_cli("watch-run", "--task-id", "partial", "--repo", str(repo), "--run-root", str(tmp_path / "runs"), "--", sys.executable, "agent.py")
    run_dir = run_dir_from(result.stdout)
    (run_dir / "command.stdout.txt").unlink()
    run = read_json(run_dir / "run.json")
    run["status"] = "completed"
    run["capture_status"] = "partial"
    (run_dir / "run.json").write_text(json.dumps(run, indent=2), encoding="utf-8")

    verify = run_cli("verify-artifacts", str(run_dir))

    assert verify.returncode == 1
    payload = read_json(run_dir / "verification" / "verify-artifacts-result.json")
    assert payload["status"] == "invalid"
    assert any("capture" in issue for issue in payload["issues"])
