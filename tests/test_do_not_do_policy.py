from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from safeloop.agent_watchdog import create_checkpoint, snapshot, snapshot_bytes, atomic_json


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
    (run_dir / "side-effects.jsonl").write_text("", encoding="utf-8")
    return run_dir


def write_policy(path: Path, rule: dict) -> Path:
    data = {"schema_version": "do-not-do-policy.v1", "policies": [{"policy_id": "p1", **rule}]}
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_file_glob_violation_detected(tmp_path: Path) -> None:
    run_dir = make_run(tmp_path, {}, {"docs/private.md": b"secret\n"})
    policy = write_policy(tmp_path / "policy.json", {"files": ["docs/*.md"]})
    result = run_cli("policy-check", str(run_dir), "--policy", str(policy))
    assert result.returncode == 1
    artifact = read_json(run_dir / "policy-check-result.json")
    assert artifact["schema_version"] == "policy-check-result.v1"
    assert artifact["violations"][0]["policy_id"] == "p1"
    assert artifact["violations"][0]["matched_files"] == ["docs/private.md"]


def test_exact_path_violation_detected(tmp_path: Path) -> None:
    run_dir = make_run(tmp_path, {"src/app.py": b"old\n"}, {"src/app.py": b"new\n"})
    policy = write_policy(tmp_path / "policy.json", {"paths": ["src/app.py"]})
    assert run_cli("policy-check", str(run_dir), "--policy", str(policy)).returncode == 1
    assert read_json(run_dir / "policy-check-result.json")["violations"][0]["matched_files"] == ["src/app.py"]


def test_lockfile_dependency_violation_detected(tmp_path: Path) -> None:
    run_dir = make_run(tmp_path, {"poetry.lock": b"a\n"}, {"poetry.lock": b"b\n"})
    policy = write_policy(tmp_path / "policy.json", {"file_kinds": ["dependency"]})
    assert run_cli("policy-check", str(run_dir), "--policy", str(policy)).returncode == 1
    assert read_json(run_dir / "policy-check-result.json")["violations"][0]["matched_files"] == ["poetry.lock"]


def test_side_effect_violation_and_manual_review_command(tmp_path: Path) -> None:
    run_dir = make_run(tmp_path, {}, {"a.txt": b"x\n"})
    (run_dir / "side-effects.jsonl").write_text(json.dumps({"type": "email", "payload": "SECRET"}) + "\n", encoding="utf-8")
    policy = write_policy(tmp_path / "policy.json", {"side_effect_class": ["email"]})
    assert run_cli("policy-check", str(run_dir), "--policy", str(policy)).returncode == 1
    artifact = read_json(run_dir / "policy-check-result.json")
    v = artifact["violations"][0]
    assert v["matched_side_effects"] == [{"type": "email"}]
    assert v["exact_rollback"] is False
    assert "safeloop review" in v["suggested_compensation_command"]
    assert "SECRET" not in json.dumps(artifact)


def test_json_output_valid_and_rollback_command_generated(tmp_path: Path) -> None:
    run_dir = make_run(tmp_path, {"a.txt": b"old\n"}, {"a.txt": b"new\n"})
    policy = write_policy(tmp_path / "policy.json", {"hunk_text_contains": ["new"]})
    result = run_cli("policy-check", str(run_dir), "--policy", str(policy), "--json")
    assert result.returncode == 1
    artifact = json.loads(result.stdout)
    assert artifact == read_json(run_dir / "policy-check-result.json")
    v = artifact["violations"][0]
    assert v["matched_hunks"][0]["hunk_id"] == "hunk-0001"
    assert v["suggested_rollback_command"] == f"safeloop rollback plan {run_dir} run-test cp-0001"


def test_policy_check_does_not_auto_apply(tmp_path: Path) -> None:
    run_dir = make_run(tmp_path, {"a.txt": b"old\n"}, {"a.txt": b"new\n"})
    policy = write_policy(tmp_path / "policy.json", {"paths": ["a.txt"]})
    assert run_cli("policy-check", str(run_dir), "--policy", str(policy)).returncode == 1
    assert not (run_dir / "rollback-result.json").exists()
    assert not (run_dir / "checkpoints" / "cp-0001" / "rollback-result.json").exists()


def test_yaml_policy_supported_or_clear_missing_dependency_error(tmp_path: Path) -> None:
    run_dir = make_run(tmp_path, {}, {"a.txt": b"x\n"})
    policy = tmp_path / "policy.yaml"
    policy.write_text('schema_version: do-not-do-policy.v1\npolicies:\n  - policy_id: p1\n    paths: [a.txt]\n', encoding="utf-8")
    result = run_cli("policy-check", str(run_dir), "--policy", str(policy), "--json")
    if result.returncode == 2:
        assert "PyYAML" in result.stderr and "YAML" in result.stderr
    else:
        assert result.returncode == 1
        assert json.loads(result.stdout)["violations"][0]["policy_id"] == "p1"
