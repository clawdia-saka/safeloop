from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import safeloop.agent_watchdog as aw
from safeloop.agent_watchdog import allocate_checkpoint_seq, snapshot, verify_run


def run_cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "safeloop.cli", *args],
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def read_json(path: Path) -> dict:
    return json.loads(path.read_text())


def test_watch_run_creates_contract_artifacts_and_captures_output(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "agent.py").write_text(
        "import sys\n"
        "print('hello stdout')\n"
        "print('hello stderr', file=sys.stderr)\n"
        "open('result.txt','w').write('v1')\n",
    )
    out = tmp_path / "runs"

    result = run_cli("watch-run", "--task-id", "demo", "--repo", str(repo), "--run-root", str(out), "--", sys.executable, "agent.py")

    assert result.returncode == 0, result.stderr
    assert "Run dir:" in result.stdout
    assert "Run id:" in result.stdout
    run_dir = Path([line.split(":", 1)[1].strip() for line in result.stdout.splitlines() if line.startswith("Run dir:")][0])
    assert run_dir.exists()
    for rel in ["run.json", "timeline.jsonl", "command.stdout.txt", "command.stderr.txt", "process-result.json", "side-effects.jsonl"]:
        assert (run_dir / rel).exists(), rel
    assert "hello stdout" in (run_dir / "command.stdout.txt").read_text()
    assert "hello stderr" in (run_dir / "command.stderr.txt").read_text()
    run_json = read_json(run_dir / "run.json")
    assert run_json["schema_version"] == "run.v1"
    assert run_json["status"] == "completed"
    assert run_json["exit_code"] == 0
    assert run_json["checkpoint_count"] == 1
    cp = run_dir / "checkpoints" / "cp-0001"
    assert read_json(cp / "restore-manifest.json")["schema_version"] == "restore-manifest.v2"
    checkpoint = read_json(cp / "checkpoint.json")
    assert checkpoint["parent_checkpoint_id"] is None
    assert checkpoint["previous_state_digest"].startswith("sha256:")
    assert checkpoint["current_state_digest"].startswith("sha256:")
    assert verify_run(run_dir)["status"] == "valid"


def test_watch_run_creates_multiple_monotonic_checkpoints_and_parent_binding(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "agent.py").write_text(
        "from pathlib import Path\nimport time\n"
        "Path('notes.txt').write_text('step 1\\n')\n"
        "time.sleep(0.25)\n"
        "Path('notes.txt').write_text('step 1\\nstep 2\\n')\n"
        "time.sleep(0.25)\n"
        "Path('notes.txt').write_text('step 1\\nstep 2\\nstep 3\\n')\n"
    )
    result = run_cli("watch-run", "--task-id", "multi", "--repo", str(repo), "--run-root", str(tmp_path / "runs"), "--debounce-ms", "20", "--", sys.executable, "agent.py")
    assert result.returncode == 0, result.stderr
    run_dir = Path([line.split(":", 1)[1].strip() for line in result.stdout.splitlines() if line.startswith("Run dir:")][0])
    checkpoints = sorted((run_dir / "checkpoints").glob("cp-*"))
    assert [cp.name for cp in checkpoints] == ["cp-0001", "cp-0002", "cp-0003"]
    assert read_json(checkpoints[0] / "checkpoint.json")["parent_checkpoint_id"] is None
    assert read_json(checkpoints[1] / "checkpoint.json")["parent_checkpoint_id"] == "cp-0001"
    assert read_json(checkpoints[2] / "checkpoint.json")["parent_checkpoint_id"] == "cp-0002"
    assert read_json(run_dir / "run.json")["checkpoint_count"] == 3
    assert verify_run(run_dir)["status"] == "valid"


def test_watch_run_failure_preserves_artifacts_and_failed_status(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "agent.py").write_text("import sys\nopen('bad.txt','w').write('x')\nsys.exit(7)\n")
    result = run_cli("watch-run", "--task-id", "fail", "--repo", str(repo), "--run-root", str(tmp_path / "runs"), "--", sys.executable, "agent.py")
    assert result.returncode == 7
    run_dir = Path([line.split(":", 1)[1].strip() for line in result.stdout.splitlines() if line.startswith("Run dir:")][0])
    assert read_json(run_dir / "run.json")["status"] == "failed"
    assert read_json(run_dir / "process-result.json")["exit_code"] == 7
    assert verify_run(run_dir)["status"] == "valid"


def test_verify_artifacts_detects_tampered_bound_artifact(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "agent.py").write_text("open('result.txt','w').write('v1')\n")
    result = run_cli("watch-run", "--task-id", "tamper", "--repo", str(repo), "--run-root", str(tmp_path / "runs"), "--", sys.executable, "agent.py")
    run_dir = Path([line.split(":", 1)[1].strip() for line in result.stdout.splitlines() if line.startswith("Run dir:")][0])
    (run_dir / "checkpoints" / "cp-0001" / "diff.patch").write_text("tampered")

    verify = run_cli("verify-artifacts", str(run_dir))

    assert verify.returncode == 1
    payload = read_json(run_dir / "verification" / "verify-artifacts-result.json")
    assert payload["status"] == "invalid"
    assert any("diff.patch" in issue for issue in payload["issues"])


def test_timeline_and_undo_operator_ux_restores_modified_and_created_files(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "notes.txt").write_text("before\n")
    (repo / "agent.py").write_text("open('notes.txt','w').write('after\\n')\nopen('result.txt','w').write('v1')\n")
    result = run_cli("watch-run", "--task-id", "demo", "--repo", str(repo), "--run-root", str(tmp_path / "runs"), "--", sys.executable, "agent.py")
    run_dir = Path([line.split(":", 1)[1].strip() for line in result.stdout.splitlines() if line.startswith("Run dir:")][0])

    timeline = run_cli("timeline", str(run_dir))
    assert timeline.returncode == 0
    assert "tamper-evident local artifacts" in timeline.stdout
    assert "cp-0001" in timeline.stdout

    run_id = read_json(run_dir / "run.json")["run_id"]
    dry = run_cli("undo", str(run_dir), run_id, "cp-0001", "--dry-run")
    assert dry.returncode == 0
    assert "Undo preflight: PASS" in dry.stdout
    assert "mode: dry-run" in dry.stdout
    assert "undo-preflight.json" in dry.stdout
    assert "rollback-plan.json" in dry.stdout

    apply = run_cli("undo", str(run_dir), run_id, "cp-0001", "--apply")
    assert apply.returncode == 0
    assert "Undo complete" in apply.stdout
    assert "mode: apply" in apply.stdout
    assert "undo-result.json" in apply.stdout
    assert not (repo / "result.txt").exists()
    assert (repo / "notes.txt").read_text() == "before\n"
    assert verify_run(run_dir)["status"] == "valid"
    timeline_json = run_cli("timeline", str(run_dir), "--json")
    assert '"undo_status": "applied"' in timeline_json.stdout


def test_security_guards_reject_path_traversal_and_unsafe_undo(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "agent.py").write_text("open('result.txt','w').write('v1')\n")

    bad_task = run_cli("watch-run", "--task-id", "../escape", "--repo", str(repo), "--run-root", str(tmp_path / "runs"), "--", sys.executable, "agent.py")
    assert bad_task.returncode != 0
    assert not (tmp_path / "escape").exists()

    result = run_cli("watch-run", "--task-id", "safe", "--repo", str(repo), "--run-root", str(tmp_path / "runs"), "--", sys.executable, "agent.py")
    assert result.returncode == 0
    run_dir = Path([line.split(":", 1)[1].strip() for line in result.stdout.splitlines() if line.startswith("Run dir:")][0])
    run_id = read_json(run_dir / "run.json")["run_id"]

    mismatch = run_cli("undo", str(run_dir), "wrong-run", "cp-0001", "--dry-run")
    assert mismatch.returncode != 0
    traversal = run_cli("undo", str(run_dir), run_id, "../cp-0001", "--dry-run")
    assert traversal.returncode != 0


def test_snapshot_skips_repo_symlink_to_external_file(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    external = tmp_path / "outside-secret.txt"
    external.write_text("do not capture\n", encoding="utf-8")
    (repo / "safe.txt").write_text("ok\n", encoding="utf-8")
    (repo / "leak.txt").symlink_to(external)

    snap = snapshot(repo)

    assert "safe.txt" in snap
    assert "leak.txt" not in snap


def test_checkpoint_sequence_allocation_is_cross_process_safe(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    repo = tmp_path / "repo"
    repo.mkdir()
    run_root = tmp_path / "runs"

    with ThreadPoolExecutor(max_workers=8) as pool:
        seqs = list(pool.map(lambda i: allocate_checkpoint_seq(run_root, repo, "task", f"run-{i}"), range(16)))

    assert sorted(seqs) == list(range(1, 17))


def test_undo_blocks_deleted_file_recreated_after_checkpoint(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "gone.txt").write_text("before\n")
    (repo / "agent.py").write_text("from pathlib import Path\nPath('gone.txt').unlink()\n")
    result = run_cli("watch-run", "--task-id", "delete", "--repo", str(repo), "--run-root", str(tmp_path / "runs"), "--", sys.executable, "agent.py")
    assert result.returncode == 0
    run_dir = Path([line.split(":", 1)[1].strip() for line in result.stdout.splitlines() if line.startswith("Run dir:")][0])
    (repo / "gone.txt").write_text("operator replacement\n")

    run_id = read_json(run_dir / "run.json")["run_id"]
    dry = run_cli("undo", str(run_dir), run_id, "cp-0001", "--dry-run")
    assert dry.returncode == 0
    assert "Undo preflight: BLOCKED" in dry.stdout
    assert "deleted_path_recreated" in dry.stdout
    apply = run_cli("undo", str(run_dir), run_id, "cp-0001", "--apply")
    assert apply.returncode == 1
    assert (repo / "gone.txt").read_text() == "operator replacement\n"


def test_undo_refuses_symlink_restore_target_without_touching_link_target(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "notes.txt").write_text("before\n")
    (repo / "agent.py").write_text("open('notes.txt','w').write('after\\n')\n")
    result = run_cli("watch-run", "--task-id", "symlink-race", "--repo", str(repo), "--run-root", str(tmp_path / "runs"), "--", sys.executable, "agent.py")
    assert result.returncode == 0
    run_dir = Path([line.split(":", 1)[1].strip() for line in result.stdout.splitlines() if line.startswith("Run dir:")][0])
    symlink_target = tmp_path / "outside-target.txt"
    symlink_target.write_text("do not overwrite\n", encoding="utf-8")
    (repo / "notes.txt").unlink()
    (repo / "notes.txt").symlink_to(symlink_target)

    run_id = read_json(run_dir / "run.json")["run_id"]
    apply = run_cli("undo", str(run_dir), run_id, "cp-0001", "--apply")

    assert apply.returncode == 1
    assert "symlink_restore_target" in apply.stdout
    assert symlink_target.read_text(encoding="utf-8") == "do not overwrite\n"


def test_undo_apply_blocks_symlink_swap_after_preflight_without_touching_target(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "notes.txt").write_text("before\n")
    (repo / "agent.py").write_text("open('notes.txt','w').write('after\\n')\n")
    result = run_cli("watch-run", "--task-id", "symlink-toctou", "--repo", str(repo), "--run-root", str(tmp_path / "runs"), "--", sys.executable, "agent.py")
    assert result.returncode == 0
    run_dir = Path([line.split(":", 1)[1].strip() for line in result.stdout.splitlines() if line.startswith("Run dir:")][0])
    symlink_target = tmp_path / "outside-target.txt"
    symlink_target.write_text("do not overwrite\n", encoding="utf-8")

    def swap_after_preflight() -> None:
        (repo / "notes.txt").unlink()
        (repo / "notes.txt").symlink_to(symlink_target)

    monkeypatch.setattr(aw, "_UNDO_APPLY_TEST_HOOK", swap_after_preflight)
    run_id = read_json(run_dir / "run.json")["run_id"]

    res = aw.undo(run_dir, run_id, "cp-0001", apply=True)

    assert res["status"] == "blocked"
    assert res["blocked_reason"] == "symlink_restore_target"
    assert symlink_target.read_text(encoding="utf-8") == "do not overwrite\n"


def test_undo_apply_blocks_intermediate_symlink_ancestor_swap_after_preflight(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a" / "d").mkdir(parents=True)
    (repo / "a" / "d" / "file.txt").write_text("before\n", encoding="utf-8")
    (repo / "agent.py").write_text("from pathlib import Path\nPath('a/d/file.txt').write_text('after\\n')\n")
    result = run_cli("watch-run", "--task-id", "ancestor-symlink-toctou", "--repo", str(repo), "--run-root", str(tmp_path / "runs"), "--", sys.executable, "agent.py")
    assert result.returncode == 0
    run_dir = Path([line.split(":", 1)[1].strip() for line in result.stdout.splitlines() if line.startswith("Run dir:")][0])
    outside = tmp_path / "outside"
    (outside / "d").mkdir(parents=True)
    (outside / "d" / "file.txt").write_text("do not overwrite\n", encoding="utf-8")

    def swap_ancestor_after_preflight() -> None:
        shutil.rmtree(repo / "a")
        (repo / "a").symlink_to(outside, target_is_directory=True)

    monkeypatch.setattr(aw, "_UNDO_APPLY_TEST_HOOK", swap_ancestor_after_preflight)
    run_id = read_json(run_dir / "run.json")["run_id"]

    res = aw.undo(run_dir, run_id, "cp-0001", apply=True)

    assert res["status"] == "blocked"
    assert res["blocked_reason"] == "symlink_restore_ancestor"
    assert (outside / "d" / "file.txt").read_text(encoding="utf-8") == "do not overwrite\n"


def test_verify_artifacts_fails_closed_when_source_evidence_is_symlink(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "tracked.txt").write_text("before\n", encoding="utf-8")
    (repo / "agent.py").write_text("from pathlib import Path\nPath('tracked.txt').write_text('after\\n')\n")
    result = run_cli("watch-run", "--task-id", "source-symlink", "--repo", str(repo), "--run-root", str(tmp_path / "runs"), "--", sys.executable, "agent.py")
    assert result.returncode == 0
    run_dir = Path([line.split(":", 1)[1].strip() for line in result.stdout.splitlines() if line.startswith("Run dir:")][0])
    (repo / "tracked.txt").unlink()
    (repo / "tracked.txt").symlink_to(tmp_path / "outside.txt")

    payload = verify_run(run_dir)

    assert payload["status"] == "invalid"
    assert "source-evidence-symlink tracked.txt" in payload["issues"]


def test_verify_artifacts_detects_preserved_mtime_source_evidence_rewrite(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "tracked.txt"
    source.write_text("before\n", encoding="utf-8")
    (repo / "agent.py").write_text("from pathlib import Path\nPath('tracked.txt').write_text('after\\n')\n")
    result = run_cli("watch-run", "--task-id", "source-rewrite", "--repo", str(repo), "--run-root", str(tmp_path / "runs"), "--", sys.executable, "agent.py")
    assert result.returncode == 0
    run_dir = Path([line.split(":", 1)[1].strip() for line in result.stdout.splitlines() if line.startswith("Run dir:")][0])
    original_stat = source.stat()
    source.write_text("rewritten with preserved mtime\n", encoding="utf-8")
    os.utime(source, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))

    payload = verify_run(run_dir)

    assert payload["status"] == "invalid"
    assert "source-evidence-rewritten-after-packet tracked.txt" in payload["issues"]


def test_verify_artifacts_fails_closed_on_duplicate_source_evidence_path(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "tracked.txt").write_text("before\n", encoding="utf-8")
    (repo / "agent.py").write_text("from pathlib import Path\nPath('tracked.txt').write_text('after\\n')\n")
    result = run_cli("watch-run", "--task-id", "source-dupe", "--repo", str(repo), "--run-root", str(tmp_path / "runs"), "--", sys.executable, "agent.py")
    assert result.returncode == 0
    run_dir = Path([line.split(":", 1)[1].strip() for line in result.stdout.splitlines() if line.startswith("Run dir:")][0])
    manifest_path = run_dir / "checkpoints" / "cp-0001" / "manifest.json"
    manifest = read_json(manifest_path)
    manifest["source_evidence"].append(dict(manifest["source_evidence"][0]))
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    payload = verify_run(run_dir)

    assert payload["status"] == "invalid"
    assert "duplicate-source-evidence-path tracked.txt" in payload["issues"]
