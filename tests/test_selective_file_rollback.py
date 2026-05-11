from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from safeloop.agent_watchdog import verify_run


def run_cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "safeloop.cli", *args], cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text())


def run_dir_from(stdout: str) -> Path:
    return Path([line.split(":", 1)[1].strip() for line in stdout.splitlines() if line.startswith("Run dir:")][0])


def make_run(tmp_path: Path, script: str, initial: dict[str, str] | None = None) -> tuple[Path, Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    for name, text in (initial or {}).items():
        p = repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    (repo / "agent.py").write_text(script)
    result = run_cli("watch-run", "--task-id", "sel", "--repo", str(repo), "--run-root", str(tmp_path / "runs"), "--", sys.executable, "agent.py")
    assert result.returncode == 0, result.stderr
    run_dir = run_dir_from(result.stdout)
    return repo, run_dir, read_json(run_dir / "run.json")["run_id"]


def plan_apply(run_dir: Path, run_id: str, *files: str, checkpoint: str | None = None) -> tuple[subprocess.CompletedProcess[str], subprocess.CompletedProcess[str]]:
    target = ["--from-checkpoint", checkpoint] if checkpoint else []
    plan = run_cli("rollback", "plan", str(run_dir), run_id, *target, "--files", *files)
    apply = run_cli("rollback", "apply", str(run_dir), run_id, *target, "--files", *files)
    return plan, apply


def test_rollback_one_modified_file_preserves_another_and_plan_shape(tmp_path: Path) -> None:
    repo, run_dir, run_id = make_run(tmp_path, "open('a.txt','w').write('after-a')\nopen('b.txt','w').write('after-b')\n", {"a.txt": "before-a", "b.txt": "before-b"})
    plan, apply = plan_apply(run_dir, run_id, "a.txt")
    assert plan.returncode == 0, plan.stderr
    artifact = read_json(run_dir / "rollback-plan.json")
    assert artifact["operation"] == "rollback_selected_files"
    assert artifact["selected_files"] == ["a.txt"]
    assert artifact["target"] == {"type": "start"}
    assert artifact["covered_local_file_changes"] == {"created": [], "modified": ["a.txt"], "deleted": []}
    assert artifact["external_side_effects"]["classification"] == "manual_review"
    assert artifact["external_side_effects"]["exact_rollback"] is False
    assert apply.returncode == 0, apply.stderr
    assert (repo / "a.txt").read_text() == "before-a"
    assert (repo / "b.txt").read_text() == "after-b"
    result = read_json(run_dir / "rollback-result.json")
    assert result["status"] == "applied"
    assert result["verification"]["status"] == "valid"


def test_selected_created_file_removed_only_and_selected_deleted_file_recreated_only(tmp_path: Path) -> None:
    repo, run_dir, run_id = make_run(tmp_path, "open('new.txt','w').write('new')\nimport os\nos.remove('gone.txt')\nopen('keep.txt','w').write('changed')\n", {"gone.txt": "restore-me", "keep.txt": "old"})
    plan, apply = plan_apply(run_dir, run_id, "new.txt", "gone.txt")
    assert plan.returncode == 0, plan.stderr
    assert apply.returncode == 0, apply.stderr
    assert not (repo / "new.txt").exists()
    assert (repo / "gone.txt").read_text() == "restore-me"
    assert (repo / "keep.txt").read_text() == "changed"


def test_current_hash_mismatch_symlink_and_missing_blob_block(tmp_path: Path) -> None:
    repo, run_dir, run_id = make_run(tmp_path, "open('a.txt','w').write('after')\nopen('link.txt','w').write('after-link')\n", {"a.txt": "before", "link.txt": "before-link"})
    assert run_cli("rollback", "plan", str(run_dir), run_id, "--files", "a.txt").returncode == 0
    (repo / "a.txt").write_text("drift")
    mismatch = run_cli("rollback", "apply", str(run_dir), run_id, "--files", "a.txt")
    assert mismatch.returncode == 1
    assert read_json(run_dir / "rollback-result.json")["blocked_reason"] == "current_hash_mismatch"
    (repo / "link.txt").unlink()
    (repo / "link.txt").symlink_to(tmp_path / "outside")
    symlink = run_cli("rollback", "plan", str(run_dir), run_id, "--files", "link.txt")
    assert symlink.returncode == 0
    assert read_json(run_dir / "rollback-plan.json")["blockers"][0]["code"] == "symlink_restore_target"
    baseline = read_json(run_dir / "baseline" / "manifest.json")
    blob = baseline["before_blobs"]["a.txt"]
    (run_dir / "baseline" / blob).unlink()
    (repo / "a.txt").write_text("after")
    missing = run_cli("rollback", "plan", str(run_dir), run_id, "--files", "a.txt")
    assert missing.returncode == 0
    assert read_json(run_dir / "rollback-plan.json")["blockers"][0]["code"] == "missing_restore_blob"


def test_dependency_warning_matching_plan_timeline_and_tamper_detection(tmp_path: Path) -> None:
    repo, run_dir, run_id = make_run(tmp_path, "open('a.txt','w').write('v1')\n", {"a.txt": "v0"})
    (repo / "agent.py").write_text("open('a.txt','w').write('v2')\n")
    second = run_cli("watch-run", "--task-id", "sel", "--repo", str(repo), "--run-root", str(tmp_path / "runs"), "--", sys.executable, "agent.py")
    # Use first run's artifacts plus manually induce later checkpoint warning by copying cp-0002 from second run is not needed; one-run multi checkpoint script is below.
    repo2, rd2, rid2 = make_run(tmp_path / "multi", "open('a.txt','w').write('v1')\nopen('a.txt','w').write('v2')\n", {"a.txt": "v0"})
    plan = run_cli("rollback", "plan", str(rd2), rid2, "--files", "a.txt")
    assert plan.returncode == 0
    assert read_json(rd2 / "rollback-plan.json")["dependency_warnings"]
    bad = run_cli("rollback", "apply", str(rd2), rid2, "--files", "other.txt")
    assert bad.returncode == 1
    assert read_json(rd2 / "rollback-result.json")["blocked_reason"] == "selected_files mismatch"
    apply = run_cli("rollback", "apply", str(rd2), rid2, "--files", "a.txt")
    assert apply.returncode == 0
    events = [json.loads(line) for line in (rd2 / "timeline.jsonl").read_text().splitlines()]
    rb = [e for e in events if e.get("type") == "rollback_applied"][-1]
    assert "rollback-result.json" in rb["payload"]["artifact_digests"]
    (rd2 / "rollback-result.json").write_text(json.dumps({"tampered": True}))
    verify = run_cli("verify-artifacts", str(rd2))
    assert verify.returncode == 1
    assert "digest mismatch rollback-result.json" in verify.stdout


def test_matching_rollback_plan_required_before_apply(tmp_path: Path) -> None:
    repo, run_dir, run_id = make_run(tmp_path, "open('a.txt','w').write('after')\n", {"a.txt": "before"})
    apply = run_cli("rollback", "apply", str(run_dir), run_id, "--files", "a.txt")
    assert apply.returncode == 1
    assert read_json(run_dir / "rollback-result.json")["blocked_reason"] == "missing rollback-plan.json"
