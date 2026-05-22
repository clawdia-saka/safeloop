from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from safeloop.operator_packet import write_operator_packet_v2
from safeloop.operator_packet_manifest import write_operator_packet_manifest, verify_operator_packet_manifest
from safeloop.quarantine import (
    QuarantineError,
    empty_quarantine,
    list_quarantine,
    purge_quarantine_item,
    put_file_in_quarantine,
    restore_quarantine_item,
    verify_quarantine_item,
)


ROOT = Path(__file__).resolve().parents[1]


def make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return workspace


def make_run_dir(tmp_path: Path) -> Path:
    run_dir = tmp_path / "run"
    (run_dir / "verification").mkdir(parents=True)
    (run_dir / "run.json").write_text(
        json.dumps({"run_id": "run-quarantine", "task_id": "quarantine", "status": "completed"}, indent=2),
        encoding="utf-8",
    )
    (run_dir / "rollback-plan.json").write_text(
        json.dumps({"schema_version": "rollback-plan.v1", "run_id": "run-quarantine", "status": "ok"}, indent=2),
        encoding="utf-8",
    )
    (run_dir / "verification" / "verify-artifacts-result.json").write_text(
        json.dumps({"schema_version": "verify-artifacts-result.v1", "status": "valid", "issues": []}, indent=2),
        encoding="utf-8",
    )
    return run_dir


def run_cli(*args: str, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    return subprocess.run(
        [sys.executable, "-m", "safeloop.cli", *args],
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
    )


def item_artifacts(run_dir: Path, item_id: str) -> tuple[Path, Path, Path, Path]:
    item_dir = run_dir / "quarantine" / "items" / item_id
    return item_dir / "item.json", item_dir / "restore-manifest.json", item_dir / "audit.jsonl", item_dir / "payload" / "file"


def test_put_moves_regular_file_into_quarantine_and_records_metadata(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    run_dir = make_run_dir(tmp_path)
    target = workspace / "notes.txt"
    target.write_text("delete me\n", encoding="utf-8")
    target.chmod(0o640)

    item = put_file_in_quarantine(target, run_dir=run_dir, workspace_root=workspace, reason="cleanup generated artifact", actor="codex")
    item_json, restore_manifest, audit, payload = item_artifacts(run_dir, item["item_id"])

    assert not target.exists()
    assert payload.read_text(encoding="utf-8") == "delete me\n"
    assert item_json.exists()
    assert restore_manifest.exists()
    assert audit.exists()
    assert (run_dir / "quarantine" / "index.jsonl").exists()
    assert item["schema_version"] == "quarantine-item.v1"
    assert item["status"] == "retained"
    assert item["action"] == "delete_file"
    assert item["original_path"] == "notes.txt"
    assert item["reason"] == "cleanup generated artifact"
    assert item["actor"] == "codex"
    assert item["sha256_before"].startswith("sha256:")
    assert item["size_bytes"] == len("delete me\n")
    assert item["restore_supported"] is True


def test_put_refuses_missing_directory_and_symlink_targets(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    run_dir = make_run_dir(tmp_path)
    directory = workspace / "dir"
    directory.mkdir()
    real_file = workspace / "real.txt"
    real_file.write_text("real\n", encoding="utf-8")
    symlink = workspace / "link.txt"
    symlink.symlink_to(real_file)

    with pytest.raises(QuarantineError, match="missing file"):
        put_file_in_quarantine(workspace / "missing.txt", run_dir=run_dir, workspace_root=workspace, reason="cleanup")
    with pytest.raises(QuarantineError, match="directories are not supported"):
        put_file_in_quarantine(directory, run_dir=run_dir, workspace_root=workspace, reason="cleanup")
    with pytest.raises(QuarantineError, match="symlinks are not supported"):
        put_file_in_quarantine(symlink, run_dir=run_dir, workspace_root=workspace, reason="cleanup")


def test_restore_restores_exact_content_and_permissions(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    run_dir = make_run_dir(tmp_path)
    target = workspace / "bin.txt"
    target.write_text("payload\n", encoding="utf-8")
    target.chmod(0o600)
    item = put_file_in_quarantine(target, run_dir=run_dir, workspace_root=workspace, reason="cleanup")

    result = restore_quarantine_item(item["item_id"], run_dir=run_dir, workspace_root=workspace)

    assert result["status"] == "restored"
    assert target.read_text(encoding="utf-8") == "payload\n"
    assert stat_permissions(target) == "0o600"
    listed = list_quarantine(run_dir)
    assert listed["items"][0]["status"] == "restored"


def test_restore_refuses_path_traversal_from_restore_manifest(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    run_dir = make_run_dir(tmp_path)
    target = workspace / "victim.txt"
    target.write_text("payload\n", encoding="utf-8")
    item = put_file_in_quarantine(target, run_dir=run_dir, workspace_root=workspace, reason="cleanup")
    _, restore_manifest_path, _, _ = item_artifacts(run_dir, item["item_id"])
    manifest = json.loads(restore_manifest_path.read_text(encoding="utf-8"))
    manifest["original_path"] = "../escape.txt"
    restore_manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    with pytest.raises(QuarantineError, match="path traversal"):
        restore_quarantine_item(item["item_id"], run_dir=run_dir, workspace_root=workspace)

    assert not (tmp_path / "escape.txt").exists()


def test_restore_refuses_overwrite_unless_explicit(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    run_dir = make_run_dir(tmp_path)
    target = workspace / "notes.txt"
    target.write_text("original\n", encoding="utf-8")
    item = put_file_in_quarantine(target, run_dir=run_dir, workspace_root=workspace, reason="cleanup")
    target.write_text("new file\n", encoding="utf-8")

    with pytest.raises(QuarantineError, match="destination exists"):
        restore_quarantine_item(item["item_id"], run_dir=run_dir, workspace_root=workspace)

    result = restore_quarantine_item(item["item_id"], run_dir=run_dir, workspace_root=workspace, overwrite=True)

    assert result["status"] == "restored"
    assert target.read_text(encoding="utf-8") == "original\n"


def test_purge_removes_payload_but_leaves_tombstone_and_audit(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    run_dir = make_run_dir(tmp_path)
    target = workspace / "old.txt"
    target.write_text("old\n", encoding="utf-8")
    item = put_file_in_quarantine(target, run_dir=run_dir, workspace_root=workspace, reason="cleanup")

    result = purge_quarantine_item(item["item_id"], run_dir=run_dir, reason="retention expired", actor="codex")
    item_json, restore_manifest, audit, payload = item_artifacts(run_dir, item["item_id"])
    tombstone = json.loads(item_json.read_text(encoding="utf-8"))

    assert result["status"] == "purged"
    assert tombstone["schema_version"] == "quarantine-tombstone.v1"
    assert tombstone["status"] == "purged"
    assert tombstone["restore_supported"] is False
    assert tombstone["purge_reason"] == "retention expired"
    assert restore_manifest.exists()
    assert audit.exists()
    assert not payload.exists()
    assert verify_quarantine_item(item["item_id"], run_dir=run_dir)["status"] == "valid"


def test_empty_only_purges_old_retained_items(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    run_dir = make_run_dir(tmp_path)
    old_file = workspace / "old.txt"
    new_file = workspace / "new.txt"
    restored_file = workspace / "restored.txt"
    old_file.write_text("old\n", encoding="utf-8")
    new_file.write_text("new\n", encoding="utf-8")
    restored_file.write_text("restored\n", encoding="utf-8")
    old_item = put_file_in_quarantine(old_file, run_dir=run_dir, workspace_root=workspace, reason="cleanup")
    new_item = put_file_in_quarantine(new_file, run_dir=run_dir, workspace_root=workspace, reason="cleanup")
    restored_item = put_file_in_quarantine(restored_file, run_dir=run_dir, workspace_root=workspace, reason="cleanup")
    restore_quarantine_item(restored_item["item_id"], run_dir=run_dir, workspace_root=workspace)
    set_captured_at(run_dir, old_item["item_id"], datetime.now(timezone.utc) - timedelta(days=45))

    result = empty_quarantine(run_dir=run_dir, older_than="30d", actor="codex")
    statuses = {item["item_id"]: item["status"] for item in list_quarantine(run_dir)["items"]}

    assert result["purged"] == [old_item["item_id"]]
    assert statuses[old_item["item_id"]] == "purged"
    assert statuses[new_item["item_id"]] == "retained"
    assert statuses[restored_item["item_id"]] == "restored"


def test_verify_detects_tampered_payload_and_list_reports_tampered(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    run_dir = make_run_dir(tmp_path)
    target = workspace / "tamper.txt"
    target.write_text("original\n", encoding="utf-8")
    item = put_file_in_quarantine(target, run_dir=run_dir, workspace_root=workspace, reason="cleanup")
    _, _, _, payload = item_artifacts(run_dir, item["item_id"])
    payload.write_text("mutated\n", encoding="utf-8")

    result = verify_quarantine_item(item["item_id"], run_dir=run_dir)

    assert result["status"] == "tampered"
    assert any("payload sha256 mismatch" in issue for issue in result["issues"])
    assert list_quarantine(run_dir)["items"][0]["status"] == "tampered"


def test_quarantine_cli_put_list_verify_restore(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    run_dir = make_run_dir(tmp_path)
    target = workspace / "cli.txt"
    target.write_text("cli\n", encoding="utf-8")

    put = run_cli("quarantine", "put", "cli.txt", "--run-dir", str(run_dir), "--reason", "cleanup", cwd=workspace)
    listed = run_cli("quarantine", "list", "--run-dir", str(run_dir), "--json", cwd=workspace)
    item_id = json.loads(listed.stdout)["items"][0]["item_id"]
    verify = run_cli("quarantine", "verify", item_id, "--run-dir", str(run_dir), cwd=workspace)
    restore = run_cli("quarantine", "restore", item_id, "--run-dir", str(run_dir), cwd=workspace)

    assert put.returncode == 0, put.stdout + put.stderr
    assert "Quarantined file:" in put.stdout
    assert listed.returncode == 0, listed.stdout + listed.stderr
    assert verify.returncode == 0, verify.stdout + verify.stderr
    assert "quarantine verify: valid" in verify.stdout
    assert restore.returncode == 0, restore.stdout + restore.stderr
    assert target.read_text(encoding="utf-8") == "cli\n"


def test_operator_manifest_includes_quarantine_metadata_and_excludes_payload(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    run_dir = make_run_dir(tmp_path)
    target = workspace / "packet.txt"
    target.write_text("packet\n", encoding="utf-8")
    item = put_file_in_quarantine(target, run_dir=run_dir, workspace_root=workspace, reason="cleanup")
    packet = write_operator_packet_v2(run_dir)

    manifest = write_operator_packet_manifest(run_dir, packet)
    result = verify_operator_packet_manifest(run_dir)
    paths = {entry["path"] for entry in manifest["source_artifacts"]}

    assert "quarantine/index.jsonl" in paths
    assert f"quarantine/items/{item['item_id']}/item.json" in paths
    assert f"quarantine/items/{item['item_id']}/restore-manifest.json" in paths
    assert f"quarantine/items/{item['item_id']}/audit.jsonl" in paths
    assert f"quarantine/items/{item['item_id']}/payload/file" not in paths
    assert result["verification"]["status"] == "valid"


def test_operator_manifest_requires_quarantine_evidence_when_quarantine_exists(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    run_dir = make_run_dir(tmp_path)
    target = workspace / "packet.txt"
    target.write_text("packet\n", encoding="utf-8")
    item = put_file_in_quarantine(target, run_dir=run_dir, workspace_root=workspace, reason="cleanup")
    packet = write_operator_packet_v2(run_dir)
    manifest = write_operator_packet_manifest(run_dir, packet)
    missing = f"quarantine/items/{item['item_id']}/audit.jsonl"
    manifest["source_artifacts"] = [entry for entry in manifest["source_artifacts"] if entry["path"] != missing]
    (run_dir / "operator-packet-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    result = verify_operator_packet_manifest(run_dir)

    assert result["verification"]["status"] == "invalid"
    assert any(f"source artifact entry missing: {missing}" in issue for issue in result["verification"]["issues"])


def stat_permissions(path: Path) -> str:
    return oct(path.stat().st_mode & 0o777)


def set_captured_at(run_dir: Path, item_id: str, captured_at: datetime) -> None:
    item_json, _, _, _ = item_artifacts(run_dir, item_id)
    data = json.loads(item_json.read_text(encoding="utf-8"))
    data["captured_at"] = captured_at.isoformat()
    item_json.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
