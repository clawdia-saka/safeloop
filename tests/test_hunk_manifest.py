from __future__ import annotations

import json
from pathlib import Path

from safeloop.agent_watchdog import create_checkpoint, snapshot, snapshot_bytes, verify_run, atomic_json, append_event, sha_file


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def make_checkpoint(tmp_path: Path, before_files: dict[str, bytes], after_files: dict[str, bytes], cid: str = "cp-0001", suffix: str = "") -> Path:
    repo = tmp_path / f"repo-{cid}{suffix}"
    run_dir = tmp_path / f"run-{cid}{suffix}"
    repo.mkdir()
    for rel, data in before_files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    before = snapshot(repo)
    before_bytes = snapshot_bytes(repo)
    for rel in before_files:
        if rel not in after_files:
            (repo / rel).unlink()
    for rel, data in after_files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    after = snapshot(repo)
    create_checkpoint(run_dir, repo, cid, 1, "test", None, before, after, before_bytes)
    return run_dir / "checkpoints" / cid


def test_modified_text_file_produces_unified_diff_and_hunk_manifest(tmp_path: Path) -> None:
    cp = make_checkpoint(tmp_path, {"notes.txt": b"one\ntwo\nthree\n"}, {"notes.txt": b"one\nTWO\nthree\n"})

    changes = (cp / "changes.patch").read_text(encoding="utf-8")
    manifest = read_json(cp / "hunk-manifest.json")

    assert "--- a/notes.txt" in changes
    assert "+++ b/notes.txt" in changes
    assert "@@ -1,3 +1,3 @@" in changes
    assert "-two" in changes
    assert "+TWO" in changes
    entry = manifest["hunks"][0]
    assert entry["hunk_id"] == "hunk-0001"
    assert entry["checkpoint_id"] == "cp-0001"
    assert entry["path"] == "notes.txt"
    assert entry["operation"] == "modified"
    assert entry["old_start"] == 1
    assert entry["old_lines"] == 3
    assert entry["new_start"] == 1
    assert entry["new_lines"] == 3
    assert entry["before_text_hash"].startswith("sha256:")
    assert entry["after_text_hash"].startswith("sha256:")
    assert entry["binary"] is False
    assert entry["undo_supported"] is True
    assert entry["unsupported_reason"] is None


def test_created_text_file_produces_hunk_entries(tmp_path: Path) -> None:
    cp = make_checkpoint(tmp_path, {}, {"new.txt": b"alpha\nbeta\n"})
    changes = (cp / "changes.patch").read_text(encoding="utf-8")
    entry = read_json(cp / "hunk-manifest.json")["hunks"][0]
    assert "--- /dev/null" in changes
    assert "+++ b/new.txt" in changes
    assert entry["operation"] == "created"
    assert entry["old_start"] == 0
    assert entry["old_lines"] == 0
    assert entry["new_lines"] == 2


def test_deleted_text_file_produces_hunk_entries(tmp_path: Path) -> None:
    cp = make_checkpoint(tmp_path, {"old.txt": b"alpha\nbeta\n"}, {})
    changes = (cp / "changes.patch").read_text(encoding="utf-8")
    entry = read_json(cp / "hunk-manifest.json")["hunks"][0]
    assert "--- a/old.txt" in changes
    assert "+++ /dev/null" in changes
    assert entry["operation"] == "deleted"
    assert entry["old_lines"] == 2
    assert entry["new_start"] == 0
    assert entry["new_lines"] == 0


def test_binary_file_marked_unsupported(tmp_path: Path) -> None:
    cp = make_checkpoint(tmp_path, {"image.bin": b"\x00\x01before"}, {"image.bin": b"\x00\x02after"})
    entry = read_json(cp / "hunk-manifest.json")["hunks"][0]
    assert "Binary files a/image.bin and b/image.bin differ" in (cp / "changes.patch").read_text(encoding="utf-8")
    assert entry["path"] == "image.bin"
    assert entry["binary"] is True
    assert entry["undo_supported"] is False
    assert entry["unsupported_reason"] == "binary_file"


def test_hunk_ids_are_deterministic_for_same_input(tmp_path: Path) -> None:
    cp1 = make_checkpoint(tmp_path, {"a.txt": b"x\ny\n"}, {"a.txt": b"x\nY\n"}, "cp-0001", "a")
    cp2 = make_checkpoint(tmp_path, {"a.txt": b"x\ny\n"}, {"a.txt": b"x\nY\n"}, "cp-0001", "b")
    assert read_json(cp1 / "hunk-manifest.json")["hunks"] == read_json(cp2 / "hunk-manifest.json")["hunks"]


def test_verify_artifacts_catches_hunk_manifest_tamper_when_bound(tmp_path: Path) -> None:
    cp = make_checkpoint(tmp_path, {"a.txt": b"x\n"}, {"a.txt": b"y\n"})
    run_dir = cp.parents[1]
    atomic_json(run_dir / "run.json", {"schema_version": "run.v1", "run_id": "run-test", "task_id": "task", "repo": str(tmp_path), "status": "completed", "capture_status": "complete", "final_event_hash": None})
    (run_dir / "command.stdout.txt").write_text("", encoding="utf-8")
    (run_dir / "command.stderr.txt").write_text("", encoding="utf-8")
    atomic_json(run_dir / "process-result.json", {"exit_code": 0})
    (run_dir / "side-effects.jsonl").write_text("", encoding="utf-8")
    timeline = run_dir / "timeline.jsonl"
    digest_names = ["checkpoint.json", "manifest.json", "diff.patch", "changes.patch", "hunk-manifest.json", "restore-manifest.json", "summary.md"]
    prev = append_event(timeline, 1, "checkpoint_created", {"checkpoint_id": "cp-0001", "parent_checkpoint_id": None, "artifact_digests": {name: sha_file(cp / name) for name in digest_names}}, None)
    run = read_json(run_dir / "run.json")
    run["final_event_hash"] = prev
    atomic_json(run_dir / "run.json", run)

    manifest = read_json(cp / "hunk-manifest.json")
    manifest["hunks"][0]["path"] = "tampered.txt"
    (cp / "hunk-manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    result = verify_run(run_dir, check_source_evidence=False)
    assert result["status"] == "invalid"
    assert "digest mismatch checkpoints/cp-0001/hunk-manifest.json" in result["issues"]
