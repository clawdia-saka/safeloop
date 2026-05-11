from __future__ import annotations

import json

from safeloop.agent_watchdog import verify_run
from safeloop.local_anchor import (
    approval_payload_hash,
    canonical_sha256,
    create_local_anchor,
    journal_hash,
    run_artifact_hashes,
    verify_local_anchor,
)


def test_canonical_hashes_are_deterministic(tmp_path):
    assert approval_payload_hash({"b": [2, 1], "a": "yes"}) == approval_payload_hash({"a": "yes", "b": [2, 1]})
    assert canonical_sha256({"x": 1}) == "sha256:5041bf1f713df204784353e82f6a4a535931cb64f1f4b4a5aeaffcb720918b22"

    journal = tmp_path / "timeline.jsonl"
    journal.write_text('{"b":2,"a":1}\n{"event":"closed","ok":true}\n', encoding="utf-8")
    reordered = tmp_path / "timeline-reordered.jsonl"
    reordered.write_text('{"a":1,"b":2}\n{"ok":true,"event":"closed"}\n', encoding="utf-8")
    assert journal_hash(journal) == journal_hash(reordered)


def _minimal_run_dir(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    event = {
        "schema_version": "timeline-event.v1",
        "event_id": "evt-0001",
        "seq": 1,
        "type": "run_closed",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "prev_event_hash": None,
        "payload": {"status": "completed"},
    }
    event["event_hash"] = canonical_sha256(event)
    (run_dir / "timeline.jsonl").write_text(json.dumps(event, sort_keys=True) + "\n", encoding="utf-8")
    run = {
        "schema_version": "run.v1",
        "run_id": "run-test",
        "task_id": "task",
        "status": "completed",
        "exit_code": 0,
        "checkpoint_count": 0,
        "capture_status": "complete",
        "final_event_hash": event["event_hash"],
    }
    (run_dir / "run.json").write_text(json.dumps(run, sort_keys=True) + "\n", encoding="utf-8")
    (run_dir / "command.stdout.txt").write_text("ok\n", encoding="utf-8")
    (run_dir / "command.stderr.txt").write_text("", encoding="utf-8")
    (run_dir / "process-result.json").write_text('{"exit_code":0}\n', encoding="utf-8")
    (run_dir / "side-effects.jsonl").write_text("", encoding="utf-8")
    return run_dir


def test_local_anchor_detects_artifact_and_journal_tampering(tmp_path):
    run_dir = _minimal_run_dir(tmp_path)
    anchor = create_local_anchor(run_dir, {"approved_by": "operator", "action": "run"})

    assert anchor["approval_payload_hash"] == approval_payload_hash({"action": "run", "approved_by": "operator"})
    assert verify_local_anchor(run_dir)["status"] == "valid"
    assert verify_run(run_dir)["status"] == "valid"

    (run_dir / "command.stdout.txt").write_text("tampered\n", encoding="utf-8")
    artifact_result = verify_local_anchor(run_dir)
    assert artifact_result["status"] == "invalid"
    assert "artifact hash mismatch command.stdout.txt" in artifact_result["issues"]

    create_local_anchor(run_dir)
    timeline = run_dir / "timeline.jsonl"
    timeline.write_text(timeline.read_text(encoding="utf-8").replace("completed", "failed"), encoding="utf-8")
    journal_result = verify_local_anchor(run_dir)
    assert journal_result["status"] == "invalid"
    assert "journal hash mismatch" in journal_result["issues"]


def test_verify_local_anchor_reports_missing_run_and_timeline_without_crashing(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "local-anchor.json").write_text(
        json.dumps({"schema_version": "local-anchor.v1", "anchor_hash": "sha256:" + "0" * 64}) + "\n",
        encoding="utf-8",
    )

    result = verify_local_anchor(run_dir)

    assert result["status"] == "invalid"
    assert "missing run.json" in result["issues"]
    assert "missing timeline.jsonl" in result["issues"]


def test_run_artifact_hashes_skips_symlink_artifacts_pointing_external(tmp_path):
    run_dir = _minimal_run_dir(tmp_path)
    external = tmp_path / "external.txt"
    external.write_text("secret\n", encoding="utf-8")
    (run_dir / "linked-artifact.txt").symlink_to(external)

    hashes = run_artifact_hashes(run_dir)

    assert "linked-artifact.txt" not in hashes
