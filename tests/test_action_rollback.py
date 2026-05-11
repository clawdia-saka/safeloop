from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(Path.cwd() / "src")}
    return subprocess.run([sys.executable, "-m", "safeloop.cli", *args], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, env=env)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text())


def run_dir_from(stdout: str) -> Path:
    return Path([line.split(":", 1)[1].strip() for line in stdout.splitlines() if line.startswith("Run dir:")][0])


def make_action_run(tmp_path: Path, script: str, initial: dict[str, str]) -> tuple[Path, Path, str, str]:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    for name, text in initial.items():
        p = repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    (repo / "agent.py").write_text(script)
    result = run_cli("watch-run", "--task-id", "action", "--repo", str(repo), "--run-root", str(tmp_path / "runs"), "--", sys.executable, "agent.py")
    assert result.returncode == 0, result.stderr
    run_dir = run_dir_from(result.stdout)
    run_id = read_json(run_dir / "run.json")["run_id"]
    events = [json.loads(line) for line in (run_dir / "action-events.jsonl").read_text().splitlines()]
    return repo, run_dir, run_id, events[0]["action_id"]


def test_action_rollback_plan_and_apply_preserves_unrelated_same_file_change(tmp_path: Path) -> None:
    before = "".join(f"line {i}\n" for i in range(1, 61))
    script = f"""
import sys
sys.path.insert(0, {str(Path.cwd() / 'src')!r})
from safeloop.action_span import action_span
text = open('notes.txt').read()
with action_span('edit early line', intent='revert only early edit'):
    open('notes.txt', 'w').write(text.replace('line 2\\n', 'LINE 2\\n'))
text = open('notes.txt').read()
open('notes.txt', 'w').write(text.replace('line 55\\n', 'LINE 55\\n'))
"""
    repo, run_dir, run_id, action_id = make_action_run(tmp_path, script, {"notes.txt": before})

    plan = run_cli("rollback", "plan", str(run_dir), run_id, "--action", action_id)
    assert plan.returncode == 0, plan.stderr
    artifact = read_json(run_dir / "rollback-plan.json")
    assert artifact["schema_version"] == "rollback-plan.v1"
    assert artifact["operation"] == "rollback_action"
    assert artifact["action_id"] == action_id
    assert artifact["action"]["name"] == "edit early line"
    assert artifact["preflight"]["status"] == "pass"
    assert artifact["external_side_effects"]["exact_rollback"] is False

    applied = run_cli("rollback", "apply", str(run_dir), run_id, "--action", action_id)
    assert applied.returncode == 0, applied.stderr
    text = (repo / "notes.txt").read_text()
    assert "line 2\n" in text
    assert "LINE 55\n" in text
    result = read_json(run_dir / "rollback-result.json")
    assert result["schema_version"] == "rollback-result.v1"
    assert result["operation"] == "rollback_action"
    assert result["status"] == "applied"
    events = [json.loads(line) for line in (run_dir / "timeline.jsonl").read_text().splitlines()]
    assert [e for e in events if e.get("type") == "rollback_applied"][-1]["payload"]["artifact_digests"]["rollback-result.json"]


def test_action_apply_requires_matching_plan(tmp_path: Path) -> None:
    script = """
from safeloop.action_span import action_span
with action_span('edit file'):
    open('a.txt', 'w').write('after')
"""
    _, run_dir, run_id, action_id = make_action_run(tmp_path, script, {"a.txt": "before"})
    blocked = run_cli("rollback", "apply", str(run_dir), run_id, "--action", action_id)
    assert blocked.returncode == 1
    assert read_json(run_dir / "rollback-result.json")["blocked_reason"] == "missing rollback-plan.json"
