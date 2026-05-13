from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "safeloop.cli", *args], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def make_run(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("before\n", encoding="utf-8")
    (repo / "app.py").write_text("print('before')\n", encoding="utf-8")
    (repo / "agent.py").write_text(
        "from pathlib import Path\n"
        "Path('README.md').write_text('after docs\\n')\n"
        "Path('app.py').write_text(\"print('after')\\n\")\n"
        "Path('tests').mkdir(exist_ok=True)\n"
        "Path('tests/test_app.py').write_text('def test_ok(): assert True\\n')\n",
        encoding="utf-8",
    )
    result = run_cli("watch-run", "--task-id", "groups-task", "--repo", str(repo), "--run-root", str(tmp_path / "runs"), "--", sys.executable, "agent.py")
    assert result.returncode == 0, result.stderr
    return Path([line.split(":", 1)[1].strip() for line in result.stdout.splitlines() if line.startswith("Run dir:")][0])


def test_explain_creates_rollback_groups_json_and_human_output(tmp_path: Path) -> None:
    run_dir = make_run(tmp_path)
    result = run_cli("explain", str(run_dir))
    assert result.returncode == 0, result.stderr
    assert "SafeLoop explain: rollback groups and recovery boundaries" in result.stdout
    assert "grp-0001" in result.stdout
    assert "rollback modes:" in result.stdout
    assert "compensation:" in result.stdout
    assert "manual handoff:" in result.stdout
    assert "action groups:" in result.stdout
    assert "actions outside the local repo" in result.stdout
    artifact = read_json(run_dir / "rollback-groups.json")
    assert artifact["schema_version"] == "rollback-groups.v1"
    assert artifact["run"]["task_id"] == "groups-task"
    assert artifact["groups"]


def test_groups_by_checkpoint_without_action_events(tmp_path: Path) -> None:
    run_dir = make_run(tmp_path)
    artifact = json.loads(run_cli("explain", str(run_dir), "--json").stdout)
    assert {g["source"] for g in artifact["groups"]} == {"checkpoint"}
    assert artifact["groups"][0]["checkpoints"] == ["cp-0001"]


def test_groups_by_action_when_action_events_exist(tmp_path: Path) -> None:
    run_dir = make_run(tmp_path)
    (run_dir / "action-events.jsonl").write_text(
        json.dumps({"action_id": "act-docs", "title": "docs update", "checkpoint_id": "cp-0001", "files": ["README.md"]}) + "\n"
        + json.dumps({"action_id": "act-code", "title": "code update", "checkpoint_id": "cp-0001", "files": ["app.py", "tests/test_app.py"]}) + "\n",
        encoding="utf-8",
    )
    artifact = json.loads(run_cli("explain", str(run_dir), "--json").stdout)
    assert [g["source"] for g in artifact["groups"]] == ["action", "action"]
    assert [g["action_id"] for g in artifact["groups"]] == ["act-docs", "act-code"]
    assert artifact["groups"][0]["files"] == ["README.md"]


def test_hunk_ids_appear_when_hunk_manifest_exists(tmp_path: Path) -> None:
    run_dir = make_run(tmp_path)
    artifact = json.loads(run_cli("explain", str(run_dir), "--json").stdout)
    hunk_ids = {h["hunk_id"] for g in artifact["groups"] for h in g["hunks"]}
    assert "hunk-0001" in hunk_ids


def test_side_effects_manual_review_exact_rollback_false(tmp_path: Path) -> None:
    run_dir = make_run(tmp_path)
    (run_dir / "side-effects.jsonl").write_text(json.dumps({"type": "email", "payload": {"secret": "raw"}}) + "\n", encoding="utf-8")
    artifact = json.loads(run_cli("explain", str(run_dir), "--json").stdout)
    assert artifact["side_effects"]["support_status"] == "manual_review"
    assert artifact["side_effects"]["exact_rollback"] is False
    assert all(g["external_side_effects"]["exact_rollback"] is False for g in artifact["groups"])
    assert all(g["risk"] in {"medium", "high"} for g in artifact["groups"])


def test_unsupported_large_file_raises_risk(tmp_path: Path) -> None:
    run_dir = make_run(tmp_path)
    cp = run_dir / "checkpoints" / "cp-0001"
    manifest = read_json(cp / "hunk-manifest.json")
    manifest["hunks"].append({"hunk_id": "hunk-large", "checkpoint_id": "cp-0001", "path": "large.bin", "operation": "modified", "binary": True, "undo_supported": False, "unsupported_reason": "large_or_binary_file"})
    (cp / "hunk-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    artifact = json.loads(run_cli("explain", str(run_dir), "--json").stdout)
    assert any(g["risk"] == "high" for g in artifact["groups"])
    assert any("unsupported_hunk" in g["blockers"] for g in artifact["groups"])


def test_json_output_excludes_raw_payloads(tmp_path: Path) -> None:
    run_dir = make_run(tmp_path)
    (run_dir / "side-effects.jsonl").write_text(json.dumps({"type": "http", "payload": {"token": "SECRET"}}) + "\n", encoding="utf-8")
    artifact = json.loads(run_cli("explain", str(run_dir), "--json").stdout)
    serialized = json.dumps(artifact)
    assert "SECRET" not in serialized
    assert "token" not in serialized
    assert artifact["side_effects"]["raw_payloads_included"] is False


def test_review_groups_alias_still_works(tmp_path: Path) -> None:
    run_dir = make_run(tmp_path)
    result = run_cli("review", str(run_dir), "--groups")
    assert result.returncode == 0, result.stderr
    assert "SafeLoop explain: rollback groups and recovery boundaries" in result.stdout
    assert (run_dir / "rollback-groups.json").exists()


def test_explain_help_uses_operator_language() -> None:
    result = run_cli("explain", "--help")
    assert result.returncode == 0, result.stderr
    assert "rollback covers verified local repo files" in result.stdout
    assert "actions outside the local repo" in result.stdout
    assert "compensation/manual handoff" in result.stdout
    assert "action groups" in result.stdout
