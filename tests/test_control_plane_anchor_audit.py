from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from safeloop.control_plane.anchor_audit import audit_control_plane_anchors, expected_anchor_records, write_anchor_jsonl
from safeloop.control_plane.registry import ApprovalEvent, ApprovalRecord, ControlPlaneRegistry
from safeloop.local_anchor import artifact_hash, canonical_sha256


def _registry(tmp_path: Path) -> Path:
    db_path = tmp_path / "control-plane.db"
    registry = ControlPlaneRegistry(db_path)
    registry.initialize()
    registry.create_approval(
        ApprovalRecord(
            approval_id="appr-1",
            requested_by="alice",
            action="deploy",
            subject="service-a",
            status="APPROVED",
            signed_payload='{"action":"deploy"}',
            signature="sig-1",
            created_at="2026-01-01T00:00:00+00:00",
        )
    )
    registry.append_approval_event(
        ApprovalEvent(
            event_id="evt-1",
            approval_id="appr-1",
            event_type="APPROVED",
            actor="bob",
            payload='{"reason":"ok"}',
            created_at="2026-01-01T00:01:00+00:00",
        )
    )
    return db_path


def test_control_plane_anchor_audit_emits_tamper_evident_local_reports(tmp_path):
    db_path = _registry(tmp_path)
    artifact = tmp_path / "packet.json"
    artifact.write_text('{"safe":true}\n', encoding="utf-8")
    anchors = tmp_path / "anchors.jsonl"
    write_anchor_jsonl(anchors, expected_anchor_records(db_path, artifacts=[artifact]))

    result = audit_control_plane_anchors(db_path, anchors, output_dir=tmp_path / "audit", artifacts=[artifact])

    assert result["status"] == "valid"
    assert result["summary"] == {"expected": 3, "observed": 3, "issues": 0}
    assert (tmp_path / "audit" / "control-plane-audit.json").exists()
    md = (tmp_path / "audit" / "control-plane-audit.md").read_text(encoding="utf-8")
    assert "tamper-evident local audit" in md
    assert "status: valid" in md


def test_control_plane_anchor_audit_detects_tamper_missing_reordered_and_stale(tmp_path):
    db_path = _registry(tmp_path)
    artifact = tmp_path / "packet.json"
    artifact.write_text('{"safe":true}\n', encoding="utf-8")
    records = expected_anchor_records(db_path, artifacts=[artifact])

    # tamper approval digest, omit event, place artifact out of order, and make it stale.
    approval = dict(records[0], digest=canonical_sha256({"tampered": True}))
    artifact_record = dict(records[2], seq=0, created_at="2025-12-31T23:59:00+00:00")
    anchors = tmp_path / "anchors.jsonl"
    write_anchor_jsonl(anchors, [artifact_record, approval])

    artifact.write_text('{"safe":false}\n', encoding="utf-8")
    result = audit_control_plane_anchors(db_path, anchors, output_dir=tmp_path / "audit", artifacts=[artifact])

    assert result["status"] == "invalid"
    assert "digest mismatch approval:appr-1" in result["issues"]
    assert "missing anchor event:evt-1" in result["issues"]
    assert "digest mismatch artifact:packet.json" in result["issues"]
    assert "reordered anchor artifact:packet.json" in result["issues"]
    assert "stale anchor artifact:packet.json" in result["issues"]
    report = json.loads((tmp_path / "audit" / "control-plane-audit.json").read_text(encoding="utf-8"))
    assert report["schema_version"] == "control-plane-audit.v1"
    assert report["status"] == "invalid"


def test_control_plane_anchor_audit_detects_malformed_jsonl(tmp_path):
    db_path = _registry(tmp_path)
    anchors = tmp_path / "anchors.jsonl"
    anchors.write_text('{"kind":"approval"}\nnot-json\n', encoding="utf-8")

    result = audit_control_plane_anchors(db_path, anchors, output_dir=tmp_path / "audit")

    assert result["status"] == "invalid"
    assert "malformed anchor jsonl line 2" in result["issues"]


def test_control_plane_anchor_audit_cli_writes_reports(tmp_path):
    db_path = _registry(tmp_path)
    anchors = tmp_path / "anchors.jsonl"
    write_anchor_jsonl(anchors, expected_anchor_records(db_path))
    audit_dir = tmp_path / "audit"

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "safeloop.cli",
            "audit-control-plane-anchors",
            "--db",
            str(db_path),
            "--anchors",
            str(anchors),
            "--output-dir",
            str(audit_dir),
        ],
        check=False,
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0
    assert "tamper-evident local audit" in completed.stdout
    assert (audit_dir / "control-plane-audit.json").exists()
    assert (audit_dir / "control-plane-audit.md").exists()
