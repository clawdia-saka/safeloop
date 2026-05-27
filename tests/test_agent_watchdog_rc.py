from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import safeloop.agent_watchdog as aw
from safeloop.agent_watchdog import allocate_checkpoint_seq, snapshot, verify_run
from safeloop.quarantine import list_quarantine
from safeloop.runtime_tool_exec import read_runtime_tool_exec_events
from safeloop.runtime_tool_firewall import read_runtime_tool_firewall_events


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


def test_watch_run_tool_shims_route_path_rm_to_quarantine_without_real_rm(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "victim.txt").write_text("keep me recoverable\n", encoding="utf-8")
    (repo / "agent.py").write_text(
        "import json, shutil, subprocess\n"
        "from pathlib import Path\n"
        "which_rm = shutil.which('rm')\n"
        "result = subprocess.run(['rm', '-f', 'victim.txt'], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)\n"
        "Path('shim-result.json').write_text(json.dumps({'which_rm': which_rm, 'returncode': result.returncode, 'stderr': result.stderr}))\n",
        encoding="utf-8",
    )

    task_id_arg = "--" + "ta" + "sk" + "-id"
    result = run_cli(
        "watch-run",
        task_id_arg,
        "shim-rm",
        "--repo",
        str(repo),
        "--run-root",
        str(tmp_path / "runs"),
        "--tool-shims",
        "--",
        sys.executable,
        "agent.py",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    run_dir = Path([line.split(":", 1)[1].strip() for line in result.stdout.splitlines() if line.startswith("Run dir:")][0])
    assert (run_dir / "tool-shims" / "bin" / "rm").exists()
    shim_result = json.loads((repo / "shim-result.json").read_text(encoding="utf-8"))
    assert shim_result["returncode"] == 1
    assert "SafeLoop tool shim blocked rm" in shim_result["stderr"]
    assert str(run_dir / "tool-shims" / "bin" / "rm") == shim_result["which_rm"]
    assert not (repo / "victim.txt").exists()
    firewall = read_runtime_tool_firewall_events(run_dir)[0]
    assert firewall["route"] == "quarantine"
    assert firewall["source"] == "exec_wrapper"
    exec_event = read_runtime_tool_exec_events(run_dir)[0]
    assert exec_event["status"] == "blocked"
    assert exec_event["firewall_event_id"] == firewall["event_id"]
    assert exec_event["block_reason"] == "firewall route quarantine does not permit execution"
    item = list_quarantine(run_dir)["items"][0]
    assert item["original_path"] == "victim.txt"
    run_json = read_json(run_dir / "run.json")
    assert run_json["policy_profile"] == "strict-local"
    assert run_json["tool_shims_enabled"] is True
    assert run_json["tool_shims"]["enabled"] is True
    assert run_json["tool_shims"]["schema_version"] == "tool-shims.v2"
    assert run_json["tool_shims"]["coverage_version"] == "v2"
    assert "rm" in run_json["tool_shims"]["tools"]
    assert "original_path" not in run_json["tool_shims"]
    assert run_json["tool_shims"]["original_path_sha256"].startswith("sha256:")
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


def test_rollback_command_alias_writes_operator_plan_and_result_artifacts(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "notes.txt").write_text("before\n")
    (repo / "agent.py").write_text("open('notes.txt','w').write('after\\n')\n")
    result = run_cli("watch-run", "--task-id", "rollback", "--repo", str(repo), "--run-root", str(tmp_path / "runs"), "--", sys.executable, "agent.py")
    assert result.returncode == 0, result.stderr
    run_dir = Path([line.split(":", 1)[1].strip() for line in result.stdout.splitlines() if line.startswith("Run dir:")][0])
    run_id = read_json(run_dir / "run.json")["run_id"]

    plan = run_cli("rollback", "plan", str(run_dir), run_id, "cp-0001")

    assert plan.returncode == 0, plan.stderr
    assert "Rollback plan: PASS" in plan.stdout
    rollback_plan = read_json(run_dir / "rollback-plan.json")
    assert rollback_plan["schema_version"] == "rollback-plan.v1"
    assert rollback_plan["operation"] == "rollback_to_checkpoint"
    assert rollback_plan["mode"] == "dry-run"
    assert rollback_plan["covered_local_file_changes"]["modified"] == ["notes.txt"]
    assert rollback_plan["external_side_effects"]["classification"] == "manual_review"
    assert rollback_plan["review_required"] is True
    assert rollback_plan["apply_command"] == f"safeloop rollback apply {run_dir} {run_id} cp-0001"

    apply = run_cli("rollback", "apply", str(run_dir), run_id, "cp-0001")

    assert apply.returncode == 0, apply.stderr
    assert "Rollback apply complete" in apply.stdout
    assert (repo / "notes.txt").read_text() == "before\n"
    rollback_result = read_json(run_dir / "checkpoints" / "cp-0001" / "rollback-result.json")
    assert rollback_result["schema_version"] == "rollback-result.v1"
    assert rollback_result["status"] == "applied"
    assert rollback_result["verification"]["status"] == "valid"
    anchor = run_cli("verify-anchor", str(run_dir))
    assert anchor.returncode == 0, anchor.stdout


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


def test_verify_artifacts_detects_newer_mtime_source_evidence_rewrite(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "tracked.txt"
    source.write_text("before\n", encoding="utf-8")
    (repo / "agent.py").write_text("from pathlib import Path\nPath('tracked.txt').write_text('after\\n')\n")
    result = run_cli("watch-run", "--task-id", "source-newer-rewrite", "--repo", str(repo), "--run-root", str(tmp_path / "runs"), "--", sys.executable, "agent.py")
    assert result.returncode == 0
    run_dir = Path([line.split(":", 1)[1].strip() for line in result.stdout.splitlines() if line.startswith("Run dir:")][0])
    source.write_text("rewritten with newer mtime\n", encoding="utf-8")

    payload = verify_run(run_dir)

    assert payload["status"] == "invalid"
    assert "source-evidence-rewritten-after-packet tracked.txt" in payload["issues"]


def test_verify_artifacts_fails_closed_when_modified_file_lacks_source_evidence(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "tracked.txt").write_text("before\n", encoding="utf-8")
    (repo / "agent.py").write_text("from pathlib import Path\nPath('tracked.txt').write_text('after\\n')\n")
    result = run_cli("watch-run", "--task-id", "missing-source-evidence", "--repo", str(repo), "--run-root", str(tmp_path / "runs"), "--", sys.executable, "agent.py")
    assert result.returncode == 0
    run_dir = Path([line.split(":", 1)[1].strip() for line in result.stdout.splitlines() if line.startswith("Run dir:")][0])
    manifest_path = run_dir / "checkpoints" / "cp-0001" / "manifest.json"
    manifest = read_json(manifest_path)
    manifest["source_evidence"] = []
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    payload = verify_run(run_dir)

    assert payload["status"] == "invalid"
    assert "missing-source-evidence tracked.txt" in payload["issues"]


def test_run_start_baseline_artifact_and_verify(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "notes.txt").write_text("before\n")
    (repo / "agent.py").write_text("open('notes.txt','w').write('after\\n')\n")
    result = run_cli("watch-run", "--task-id", "baseline", "--repo", str(repo), "--run-root", str(tmp_path / "runs"), "--", sys.executable, "agent.py")
    assert result.returncode == 0, result.stderr
    run_dir = Path([line.split(":", 1)[1].strip() for line in result.stdout.splitlines() if line.startswith("Run dir:")][0])
    run = read_json(run_dir / "run.json")
    baseline = read_json(run_dir / "baseline" / "baseline.json")
    manifest = read_json(run_dir / "baseline" / "manifest.json")
    assert baseline["schema_version"] == "run-start-baseline.v1"
    assert baseline["run_id"] == run["run_id"]
    assert baseline["task_id"] == "baseline"
    assert baseline["repo"] == str(repo.resolve())
    assert manifest["files"]["notes.txt"].startswith("sha256:")
    assert baseline["state_digest"].startswith("sha256:")
    assert verify_run(run_dir)["status"] == "valid"


def test_rollback_to_start_plan_and_apply_restores_whole_run(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "modified.txt").write_text("before\n")
    (repo / "deleted.txt").write_text("restore me\n")
    (repo / "agent.py").write_text("from pathlib import Path\nPath('modified.txt').write_text('after\\n')\nPath('created.txt').write_text('new\\n')\nPath('deleted.txt').unlink()\n")
    result = run_cli("watch-run", "--task-id", "to-start", "--repo", str(repo), "--run-root", str(tmp_path / "runs"), "--", sys.executable, "agent.py")
    assert result.returncode == 0, result.stderr
    run_dir = Path([line.split(":", 1)[1].strip() for line in result.stdout.splitlines() if line.startswith("Run dir:")][0])
    run_id = read_json(run_dir / "run.json")["run_id"]

    plan = run_cli("rollback", "plan", str(run_dir), run_id, "--to-start")
    assert plan.returncode == 0, plan.stderr
    payload = read_json(run_dir / "rollback-plan.json")
    assert payload["schema_version"] == "rollback-plan.v1"
    assert payload["operation"] == "rollback_to_start"
    assert payload["covered_local_file_changes"] == {"created": ["created.txt"], "modified": ["modified.txt"], "deleted": ["deleted.txt"]}
    assert payload["external_side_effects"] == {"classification": "manual_review", "exact_rollback": False, "note": "External side effects are not exact rollback; review side-effects.jsonl for compensation or handoff."}

    apply = run_cli("rollback", "apply", str(run_dir), run_id, "--to-start")
    assert apply.returncode == 0, apply.stderr
    assert not (repo / "created.txt").exists()
    assert (repo / "modified.txt").read_text() == "before\n"
    assert (repo / "deleted.txt").read_text() == "restore me\n"
    result_payload = read_json(run_dir / "rollback-result.json")
    assert result_payload["schema_version"] == "rollback-result.v1"
    assert result_payload["status"] == "applied"
    assert result_payload["verification"]["status"] == "valid"
    assert verify_run(run_dir, check_source_evidence=False)["status"] == "valid"
    assert any(e["type"] == "rollback_applied" for e in aw.timeline_events(run_dir))


def test_readme_quickstart_post_rollback_verify_artifacts_is_not_confusing_invalid(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "note.txt").write_text("base\n", encoding="utf-8")
    (repo / "agent.py").write_text("from pathlib import Path\nPath('note.txt').write_text('changed\\n')\n")
    result = run_cli("watch-run", "--task-id", "quickstart-smoke", "--repo", str(repo), "--run-root", str(tmp_path / "runs"), "--", sys.executable, "agent.py")
    assert result.returncode == 0, result.stderr
    run_dir = Path([line.split(":", 1)[1].strip() for line in result.stdout.splitlines() if line.startswith("Run dir:")][0])
    run_id = read_json(run_dir / "run.json")["run_id"]

    plan = run_cli("rollback", "plan", str(run_dir), run_id, "--to-start")
    assert plan.returncode == 0, plan.stderr
    apply = run_cli("rollback", "apply", str(run_dir), run_id, "--to-start")
    assert apply.returncode == 0, apply.stderr
    assert (repo / "note.txt").read_text(encoding="utf-8") == "base\n"

    verify = run_cli("verify-artifacts", str(run_dir))

    assert verify.returncode == 0, verify.stdout + verify.stderr
    payload = read_json(run_dir / "verification" / "verify-artifacts-result.json")
    assert payload["status"] == "valid"
    assert "source-evidence-rewritten-after-packet note.txt" not in payload["issues"]


def test_verify_artifacts_does_not_trust_unbound_root_rollback_result_to_skip_source_digest(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "tracked.txt"
    source.write_text("before\n", encoding="utf-8")
    (repo / "agent.py").write_text("from pathlib import Path\nPath('tracked.txt').write_text('after\\n')\n")
    result = run_cli("watch-run", "--task-id", "forged-root-rollback", "--repo", str(repo), "--run-root", str(tmp_path / "runs"), "--", sys.executable, "agent.py")
    assert result.returncode == 0, result.stderr
    run_dir = Path([line.split(":", 1)[1].strip() for line in result.stdout.splitlines() if line.startswith("Run dir:")][0])
    run_id = read_json(run_dir / "run.json")["run_id"]
    source.write_text("tampered after packet\n", encoding="utf-8")
    (run_dir / "rollback-result.json").write_text(
        json.dumps({"schema_version": "rollback-result.v1", "operation": "rollback_to_start", "run_id": run_id, "mode": "apply", "status": "applied"}, indent=2),
        encoding="utf-8",
    )

    payload = verify_run(run_dir)

    assert payload["status"] == "invalid"
    assert "source-evidence-rewritten-after-packet tracked.txt" in payload["issues"]


def test_verify_artifacts_fails_closed_on_malformed_root_rollback_result(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "tracked.txt"
    source.write_text("before\n", encoding="utf-8")
    (repo / "agent.py").write_text("from pathlib import Path\nPath('tracked.txt').write_text('after\\n')\n")
    result = run_cli("watch-run", "--task-id", "malformed-root-rollback", "--repo", str(repo), "--run-root", str(tmp_path / "runs"), "--", sys.executable, "agent.py")
    assert result.returncode == 0, result.stderr
    run_dir = Path([line.split(":", 1)[1].strip() for line in result.stdout.splitlines() if line.startswith("Run dir:")][0])
    source.write_text("tampered after packet\n", encoding="utf-8")

    (run_dir / "rollback-result.json").write_text("{not-json}", encoding="utf-8")
    invalid_json_payload = verify_run(run_dir)
    assert invalid_json_payload["status"] == "invalid"
    assert "malformed rollback-result.json" in invalid_json_payload["issues"]
    assert "source-evidence-rewritten-after-packet tracked.txt" in invalid_json_payload["issues"]

    (run_dir / "rollback-result.json").write_text("[]", encoding="utf-8")
    non_object_payload = verify_run(run_dir)
    assert non_object_payload["status"] == "invalid"
    assert "malformed rollback-result.json" in non_object_payload["issues"]
    assert "source-evidence-rewritten-after-packet tracked.txt" in non_object_payload["issues"]


def test_rollback_to_start_blocks_symlink_target(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "notes.txt").write_text("before\n")
    (repo / "agent.py").write_text("open('notes.txt','w').write('after\\n')\n")
    result = run_cli("watch-run", "--task-id", "to-start-link", "--repo", str(repo), "--run-root", str(tmp_path / "runs"), "--", sys.executable, "agent.py")
    assert result.returncode == 0
    run_dir = Path([line.split(":", 1)[1].strip() for line in result.stdout.splitlines() if line.startswith("Run dir:")][0])
    run_id = read_json(run_dir / "run.json")["run_id"]
    plan = run_cli("rollback", "plan", str(run_dir), run_id, "--to-start")
    assert plan.returncode == 0, plan.stderr
    outside = tmp_path / "outside.txt"
    outside.write_text("safe\n")
    (repo / "notes.txt").unlink()
    (repo / "notes.txt").symlink_to(outside)
    apply = run_cli("rollback", "apply", str(run_dir), run_id, "--to-start")
    assert apply.returncode == 1
    assert "symlink_restore_target" in apply.stdout
    assert outside.read_text() == "safe\n"


def test_rollback_to_start_blocks_large_baseline_blob(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(aw, "BASELINE_MAX_BLOB_BYTES", 4)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "big.txt").write_text("before-big\n")
    (repo / "agent.py").write_text("open('big.txt','w').write('after\\n')\n")
    code, run_dir = aw.watch_run("large", repo, [sys.executable, "agent.py"], tmp_path / "runs")
    assert code == 0
    run_id = read_json(run_dir / "run.json")["run_id"]
    res = aw.rollback_to_start(run_dir, run_id, apply=False)
    assert res["status"] == "blocked"
    assert res["blocked_reason"] == "large_file_unsupported"


def _make_rollback_run(tmp_path: Path) -> tuple[Path, Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "notes.txt").write_text("before\n")
    (repo / "agent.py").write_text("from pathlib import Path\nPath('notes.txt').write_text('after\\n')\n")
    result = run_cli("watch-run", "--task-id", "rollback-guard", "--repo", str(repo), "--run-root", str(tmp_path / "runs"), "--", sys.executable, "agent.py")
    assert result.returncode == 0, result.stderr
    run_dir = Path([line.split(":", 1)[1].strip() for line in result.stdout.splitlines() if line.startswith("Run dir:")][0])
    run_id = read_json(run_dir / "run.json")["run_id"]
    return repo, run_dir, run_id


def _timeline_types(run_dir: Path) -> list[str]:
    return [json.loads(line)["type"] for line in (run_dir / "timeline.jsonl").read_text().splitlines() if line.strip()]


def test_rollback_apply_without_plan_blocks(tmp_path: Path) -> None:
    _, run_dir, run_id = _make_rollback_run(tmp_path)
    result = run_cli("rollback", "apply", str(run_dir), run_id, "cp-0001")
    assert result.returncode == 1
    assert "missing rollback-plan.json" in result.stdout
    assert "rollback_blocked" in _timeline_types(run_dir)


def test_rollback_apply_with_mismatched_run_id_blocks(tmp_path: Path) -> None:
    _, run_dir, run_id = _make_rollback_run(tmp_path)
    assert run_cli("rollback", "plan", str(run_dir), run_id, "cp-0001").returncode == 0
    plan = read_json(run_dir / "rollback-plan.json")
    plan["run_id"] = "wrong-run"
    (run_dir / "rollback-plan.json").write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n")
    result = run_cli("rollback", "apply", str(run_dir), run_id, "cp-0001")
    assert result.returncode == 1
    assert "run_id mismatch" in result.stdout


def test_rollback_apply_with_mismatched_checkpoint_target_blocks(tmp_path: Path) -> None:
    _, run_dir, run_id = _make_rollback_run(tmp_path)
    assert run_cli("rollback", "plan", str(run_dir), run_id, "cp-0001").returncode == 0
    plan = read_json(run_dir / "rollback-plan.json")
    plan["checkpoint_id"] = "cp-9999"
    (run_dir / "rollback-plan.json").write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n")
    result = run_cli("rollback", "apply", str(run_dir), run_id, "cp-0001")
    assert result.returncode == 1
    assert "target mismatch" in result.stdout


def test_rollback_apply_with_matching_plan_succeeds_and_timeline_binds_digests(tmp_path: Path) -> None:
    repo, run_dir, run_id = _make_rollback_run(tmp_path)
    plan = run_cli("rollback", "plan", str(run_dir), run_id, "cp-0001")
    assert plan.returncode == 0, plan.stderr
    assert "rollback_plan_created" in _timeline_types(run_dir)
    apply = run_cli("rollback", "apply", str(run_dir), run_id, "cp-0001")
    assert apply.returncode == 0, apply.stderr
    assert (repo / "notes.txt").read_text() == "before\n"
    events = [json.loads(line) for line in (run_dir / "timeline.jsonl").read_text().splitlines() if line.strip()]
    assert "rollback_applied" in [event["type"] for event in events]
    applied = [event for event in events if event["type"] == "rollback_applied"][-1]
    digests = applied["payload"]["artifact_digests"]
    assert set(digests) == {"rollback-plan.json", "rollback-result.json"}
    assert digests["rollback-plan.json"].startswith("sha256:")
    assert digests["rollback-result.json"].startswith("sha256:")
    assert verify_run(run_dir)["status"] == "valid"


def test_verify_artifacts_detects_rollback_result_tampering(tmp_path: Path) -> None:
    _, run_dir, run_id = _make_rollback_run(tmp_path)
    assert run_cli("rollback", "plan", str(run_dir), run_id, "cp-0001").returncode == 0
    assert run_cli("rollback", "apply", str(run_dir), run_id, "cp-0001").returncode == 0
    result_path = run_dir / "checkpoints" / "cp-0001" / "rollback-result.json"
    payload = read_json(result_path)
    payload["status"] = "tampered"
    result_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    verify = run_cli("verify-artifacts", str(run_dir))
    assert verify.returncode == 1
    assert "digest mismatch checkpoints/cp-0001/rollback-result.json" in verify.stdout


def test_rollback_to_start_requires_matching_plan(tmp_path: Path) -> None:
    repo, run_dir, run_id = _make_rollback_run(tmp_path)
    missing = run_cli("rollback", "apply", str(run_dir), run_id, "--to-start")
    assert missing.returncode == 1
    assert "missing rollback-plan.json" in missing.stdout
    plan = run_cli("rollback", "plan", str(run_dir), run_id, "--to-start")
    assert plan.returncode == 0, plan.stderr
    apply = run_cli("rollback", "apply", str(run_dir), run_id, "--to-start")
    assert apply.returncode == 0, apply.stderr
    assert (repo / "notes.txt").read_text() == "before\n"


def test_changed_only_restore_blobs_large_modified_blocks_rollback(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(aw, "MAX_RESTORE_BLOB_BYTES", 4)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "big.txt").write_text("before-big\n")
    (repo / "agent.py").write_text("open('big.txt','w').write('after\\n')\n")

    code, run_dir = aw.watch_run("large-mod", repo, [sys.executable, "agent.py"], tmp_path / "runs")

    assert code == 0
    restore = read_json(run_dir / "checkpoints" / "cp-0001" / "restore-manifest.json")
    assert restore["before_blobs"]["big.txt"] is None
    assert restore["restore_entries"]["big.txt"]["undo_supported"] is False
    assert restore["restore_entries"]["big.txt"]["unsupported_reason"] == "restore_blob_too_large"
    assert verify_run(run_dir)["status"] == "valid"
    run_id = read_json(run_dir / "run.json")["run_id"]
    plan = aw.undo(run_dir, run_id, "cp-0001", apply=False)
    assert plan["status"] == "blocked"
    assert plan["blocked_reason"] == "restore_blob_too_large"
    apply = aw.undo(run_dir, run_id, "cp-0001", apply=True)
    assert apply["status"] == "blocked"
    assert (repo / "big.txt").read_text() == "after\n"


def test_changed_only_restore_blobs_large_deleted_blocks_rollback(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(aw, "MAX_RESTORE_BLOB_BYTES", 4)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "big.txt").write_text("before-big\n")
    (repo / "agent.py").write_text("from pathlib import Path\nPath('big.txt').unlink()\n")

    code, run_dir = aw.watch_run("large-del", repo, [sys.executable, "agent.py"], tmp_path / "runs")

    assert code == 0
    restore = read_json(run_dir / "checkpoints" / "cp-0001" / "restore-manifest.json")
    assert restore["before_blobs"]["big.txt"] is None
    assert restore["restore_entries"]["big.txt"]["unsupported_reason"] == "restore_blob_too_large"
    run_id = read_json(run_dir / "run.json")["run_id"]
    res = aw.undo(run_dir, run_id, "cp-0001", apply=False)
    assert res["status"] == "blocked"
    assert res["blocked_reason"] == "restore_blob_too_large"
    assert not (repo / "big.txt").exists()


def test_changed_only_restore_blobs_small_deleted_restores(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "gone.txt").write_text("before\n")
    (repo / "agent.py").write_text("from pathlib import Path\nPath('gone.txt').unlink()\n")
    code, run_dir = aw.watch_run("small-del", repo, [sys.executable, "agent.py"], tmp_path / "runs")
    assert code == 0
    run_id = read_json(run_dir / "run.json")["run_id"]
    res = aw.undo(run_dir, run_id, "cp-0001", apply=True)
    assert res["status"] == "applied"
    assert (repo / "gone.txt").read_text() == "before\n"


def test_snapshot_uses_discover_repo_files_gitignore_and_untracked(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE)
    (repo / ".gitignore").write_text("ignored.txt\n")
    (repo / "tracked.txt").write_text("tracked\n")
    subprocess.run(["git", "add", ".gitignore", "tracked.txt"], cwd=repo, check=True)
    (repo / "untracked.txt").write_text("untracked\n")
    (repo / "ignored.txt").write_text("ignored\n")
    snap = snapshot(repo)
    assert "tracked.txt" in snap
    assert "untracked.txt" in snap
    assert "ignored.txt" not in snap
