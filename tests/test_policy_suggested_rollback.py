from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from safeloop.agent_watchdog import append_event, atomic_json, create_checkpoint, snapshot, snapshot_bytes


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "safeloop.cli", *args], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def make_run(tmp_path: Path, before: dict[str, bytes], after: dict[str, bytes]) -> Path:
    repo = tmp_path / "repo"
    run_dir = tmp_path / "run"
    repo.mkdir()
    for rel, data in before.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    before_snap = snapshot(repo)
    before_bytes = snapshot_bytes(repo)
    for rel in before:
        if rel not in after:
            (repo / rel).unlink()
    for rel, data in after.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    after_snap = snapshot(repo)
    create_checkpoint(run_dir, repo, "cp-0001", 1, "change", None, before_snap, after_snap, before_bytes)
    atomic_json(run_dir / "run.json", {"schema_version": "run.v1", "run_id": "run-test", "task_id": "task", "repo": str(repo), "status": "completed", "checkpoint_count": 1})
    append_event(run_dir / "timeline.jsonl", 1, "checkpoint", {"checkpoint_id": "cp-0001"}, None)
    (run_dir / "side-effects.jsonl").write_text("", encoding="utf-8")
    return run_dir


def write_policy(path: Path, rule: dict) -> Path:
    data = {"schema_version": "do-not-do-policy.v1", "policies": [{"policy_id": "p1", **rule}]}
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_readme_file_policy_suggests_files_rollback(tmp_path: Path) -> None:
    run_dir = make_run(tmp_path, {"README.md": b"old\n"}, {"README.md": b"new\n"})
    policy = write_policy(tmp_path / "policy.yaml", {"paths": ["README.md"]})
    result = run_cli("policy-check", str(run_dir), "--policy", str(policy), "--suggest-rollback", "--json")
    assert result.returncode == 1
    artifact = json.loads(result.stdout)
    assert artifact["schema_version"] == "policy-rollback-suggestion.v1"
    assert artifact["local_exact_rollback"][0]["type"] == "files"
    assert "--files README.md" in artifact["local_exact_rollback"][0]["plan_command"]


def test_hunk_text_policy_suggests_hunks(tmp_path: Path) -> None:
    run_dir = make_run(tmp_path, {"a.txt": b"old\n"}, {"a.txt": b"new forbidden\n"})
    policy = write_policy(tmp_path / "policy.json", {"hunk_text_contains": ["forbidden"]})
    assert run_cli("policy-check", str(run_dir), "--policy", str(policy), "--suggest-rollback").returncode == 1
    artifact = read_json(run_dir / "policy-rollback-suggestion.json")
    assert artifact["local_exact_rollback"][0]["type"] == "hunks"
    assert "--hunks hunk-0001" in artifact["local_exact_rollback"][0]["plan_command"]


def test_action_policy_suggests_action_manual_review(tmp_path: Path) -> None:
    run_dir = make_run(tmp_path, {}, {"a.txt": b"x\n"})
    append_event(run_dir / "timeline.jsonl", 1, "action", {"action_name": "deploy_prod"}, None)
    policy = write_policy(tmp_path / "policy.json", {"action_name": ["deploy_prod"]})
    assert run_cli("policy-check", str(run_dir), "--policy", str(policy), "--suggest-rollback").returncode == 1
    artifact = read_json(run_dir / "policy-rollback-suggestion.json")
    assert artifact["local_exact_rollback"][0]["type"] == "action"
    assert "--action deploy_prod" in artifact["local_exact_rollback"][0]["plan_command"]
    assert artifact["manual_review_required"] is True


def test_side_effect_policy_suggests_compensation_without_raw_payload(tmp_path: Path) -> None:
    run_dir = make_run(tmp_path, {}, {"a.txt": b"x\n"})
    (run_dir / "side-effects.jsonl").write_text(json.dumps({"type": "email", "payload": "SECRET"}) + "\n", encoding="utf-8")
    policy = write_policy(tmp_path / "policy.json", {"side_effect_class": ["email"]})
    assert run_cli("policy-check", str(run_dir), "--policy", str(policy), "--suggest-rollback").returncode == 1
    artifact = read_json(run_dir / "policy-rollback-suggestion.json")
    assert artifact["external_compensation"][0]["exact_rollback"] is False
    assert artifact["external_compensation"][0]["manual_review_required"] is True
    assert "SECRET" not in json.dumps(artifact)


def test_mixed_local_external_marks_global_not_exact(tmp_path: Path) -> None:
    run_dir = make_run(tmp_path, {"README.md": b"old\n"}, {"README.md": b"new\n"})
    (run_dir / "side-effects.jsonl").write_text(json.dumps({"type": "email", "payload": "SECRET"}) + "\n", encoding="utf-8")
    policy = write_policy(tmp_path / "policy.json", {"paths": ["README.md"], "side_effect_class": ["email"]})
    run_cli("policy-check", str(run_dir), "--policy", str(policy), "--suggest-rollback")
    artifact = read_json(run_dir / "policy-rollback-suggestion.json")
    assert artifact["local_exact_rollback"]
    assert artifact["external_compensation"]
    assert artifact["exact_rollback"] is False


def test_no_auto_apply_and_suggested_commands_valid(tmp_path: Path) -> None:
    run_dir = make_run(tmp_path, {"README.md": b"old\n"}, {"README.md": b"new\n"})
    policy = write_policy(tmp_path / "policy.json", {"paths": ["README.md"]})
    run_cli("policy-check", str(run_dir), "--policy", str(policy), "--suggest-rollback")
    artifact = read_json(run_dir / "policy-rollback-suggestion.json")
    assert artifact["auto_apply"] is False
    assert not (run_dir / "rollback-result.json").exists()
    cmd = artifact["local_exact_rollback"][0]["plan_command"].split()
    assert run_cli(*cmd[1:]).returncode == 0
    assert (run_dir / "rollback-plan.json").exists()


def test_raw_payloads_absent(tmp_path: Path) -> None:
    run_dir = make_run(tmp_path, {"a.txt": b"old\n"}, {"a.txt": b"new SECRET\n"})
    policy = write_policy(tmp_path / "policy.json", {"hunk_text_contains": ["SECRET"]})
    run_cli("policy-check", str(run_dir), "--policy", str(policy), "--suggest-rollback")
    assert "SECRET" not in json.dumps(read_json(run_dir / "policy-rollback-suggestion.json"))
