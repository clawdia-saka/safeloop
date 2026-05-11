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


def make_run(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "notes.txt").write_text("before\n", encoding="utf-8")
    (repo / "agent.py").write_text(
        "from pathlib import Path\n"
        "Path('notes.txt').write_text('after\\n')\n"
        "Path('created.txt').write_text('new\\n')\n",
        encoding="utf-8",
    )
    result = run_cli(
        "watch-run",
        "--task-id",
        "review-task",
        "--repo",
        str(repo),
        "--run-root",
        str(tmp_path / "runs"),
        "--",
        sys.executable,
        "agent.py",
    )
    assert result.returncode == 0, result.stderr
    return Path([line.split(":", 1)[1].strip() for line in result.stdout.splitlines() if line.startswith("Run dir:")][0])


def test_review_cli_lists_checkpoints(tmp_path: Path) -> None:
    run_dir = make_run(tmp_path)

    result = run_cli("review", str(run_dir))

    assert result.returncode == 0, result.stderr
    assert "SafeLoop rollback review" in result.stdout
    assert "run id:" in result.stdout
    assert "task id: review-task" in result.stdout
    assert "checkpoints:" in result.stdout
    assert "cp-0001" in result.stdout
    assert "changed files:" in result.stdout
    assert "notes.txt" in result.stdout
    assert "created.txt" in result.stdout


def test_review_cli_lists_rollback_command_suggestions(tmp_path: Path) -> None:
    run_dir = make_run(tmp_path)
    run_id = read_json(run_dir / "run.json")["run_id"]

    result = run_cli("review", str(run_dir))

    assert result.returncode == 0, result.stderr
    assert f"safeloop rollback plan {run_dir} {run_id} cp-0001" in result.stdout
    assert f"safeloop rollback apply {run_dir} {run_id} cp-0001" in result.stdout
    assert f"safeloop rollback plan {run_dir} {run_id} --to-start" in result.stdout
    assert f"safeloop rollback apply {run_dir} {run_id} --to-start" in result.stdout


def test_review_json_is_valid(tmp_path: Path) -> None:
    run_dir = make_run(tmp_path)

    result = run_cli("review", str(run_dir), "--json")

    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["schema_version"] == "review-summary.v1"
    assert summary["run"]["task_id"] == "review-task"
    assert summary["checkpoints"][0]["checkpoint_id"] == "cp-0001"
    assert summary["recommended_commands"][0]["command"].startswith("safeloop rollback plan")
    assert (run_dir / "review-summary.json").exists()
    assert read_json(run_dir / "review-summary.json") == summary


def test_review_json_does_not_include_raw_sensitive_payload(tmp_path: Path) -> None:
    run_dir = make_run(tmp_path)
    (run_dir / "side-effects.jsonl").write_text(
        json.dumps({"type": "http", "url": "https://api.example.invalid", "metadata": {"redacted": True}}) + "\n",
        encoding="utf-8",
    )

    result = run_cli("review", str(run_dir), "--json")

    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    serialized = json.dumps(summary)
    assert "metadata" not in serialized
    assert "redacted" not in serialized
    assert summary["side_effects"]["count"] == 1
    assert summary["side_effects"]["raw_payloads_included"] is False


def test_side_effects_displayed_as_not_exact_rollback(tmp_path: Path) -> None:
    run_dir = make_run(tmp_path)
    (run_dir / "side-effects.jsonl").write_text(json.dumps({"type": "email", "to": "ops@example.invalid", "payload": "raw"}) + "\n", encoding="utf-8")

    text = run_cli("review", str(run_dir))
    data = run_cli("review", str(run_dir), "--json")

    assert text.returncode == 0, text.stderr
    assert "manual_review" in text.stdout
    assert "compensation_required" in text.stdout
    assert "exact rollback: false" in text.stdout
    summary = json.loads(data.stdout)
    assert summary["side_effects"]["support_status"] == "manual_review"
    assert summary["side_effects"]["compensation_required"] is True
    assert summary["side_effects"]["exact_rollback"] is False
