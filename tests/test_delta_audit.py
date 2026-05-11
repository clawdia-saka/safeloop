import json
from pathlib import Path

from safeloop.delta_audit import build_delta_audit_packet, verify_delta_audit_packet


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_delta_audit_packet_binds_existing_run_evidence_without_action_required(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "run-123"
    run_dir.mkdir(parents=True)
    (run_dir / "api-trace.json").write_text(
        json.dumps({
            "events": [
                {"stage": "request", "hash": "r", "artifact_sha256": "a" * 64},
                {"stage": "runtime", "hash": "r", "artifact_sha256": "b" * 64},
                {"stage": "enforcement", "hash": "e", "artifact_sha256": "c" * 64},
                {"stage": "response", "hash": "s", "artifact_sha256": "d" * 64},
            ]
        }),
        encoding="utf-8",
    )
    (run_dir / "side-effects-ledger.json").write_text(json.dumps({"effects": [{"kind": "file_write"}]}), encoding="utf-8")
    (run_dir / "pr-lifecycle.json").write_text(json.dumps({"pr": 7, "state": "merged"}), encoding="utf-8")

    packet = build_delta_audit_packet(run_dir, output_dir=tmp_path / "packet")

    assert packet["action_required"] is False
    assert packet["issues"] == []
    manifest = read_json(tmp_path / "packet" / "manifest.json")
    bundle = read_json(tmp_path / "packet" / "evidence-bundle.json")
    brief = (tmp_path / "packet" / "brief.md").read_text(encoding="utf-8")

    assert [item["path"] for item in manifest["source_evidence"]] == [
        "api-trace.json",
        "side-effects-ledger.json",
        "pr-lifecycle.json",
    ]
    assert {item["kind"] for item in manifest["source_evidence"]} == {"api_trace", "side_effects", "pr_lifecycle"}
    assert bundle["bound_evidence"] == manifest["source_evidence"]
    assert bundle["artifacts"]["api_trace"]["payload"]["events"][0]["stage"] == "request"
    assert bundle["artifacts"]["side_effects"]["payload"]["effects"][0]["kind"] == "file_write"
    assert bundle["artifacts"]["pr_lifecycle"]["payload"]["state"] == "merged"
    assert "api-trace.json" in brief
    assert "side-effects-ledger.json" in brief
    assert "pr-lifecycle.json" in brief
    assert "Action required: no" in brief
    verification = verify_delta_audit_packet(tmp_path / "packet")
    assert verification["status"] == "valid"
    assert set(manifest["packet_file_digests"]) == {"evidence-bundle.json", "brief.md"}


def test_delta_audit_packet_marks_action_required_for_malformed_present_evidence(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-bad"
    run_dir.mkdir()
    (run_dir / "api-trace.json").write_text("{not json", encoding="utf-8")

    packet = build_delta_audit_packet(run_dir, output_dir=tmp_path / "bad-packet")

    assert packet["action_required"] is True
    assert "malformed evidence api-trace.json" in packet["issues"]
    manifest = read_json(tmp_path / "bad-packet" / "manifest.json")
    assert manifest["source_evidence"] == []
    assert manifest["issues"] == ["malformed evidence api-trace.json"]
    brief = (tmp_path / "bad-packet" / "brief.md").read_text(encoding="utf-8")
    assert "Action required: yes" in brief
    assert "malformed evidence api-trace.json" in brief


def test_delta_audit_packet_requires_api_trace_evidence(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-missing-api"
    run_dir.mkdir()
    (run_dir / "side-effects-ledger.json").write_text(json.dumps({"effects": []}), encoding="utf-8")

    packet = build_delta_audit_packet(run_dir, output_dir=tmp_path / "missing-api-packet")

    assert packet["action_required"] is True
    assert "missing evidence api-trace.json" in packet["issues"]


def test_delta_audit_packet_requires_digest_bound_api_trace_stages(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-incomplete-api"
    run_dir.mkdir()
    (run_dir / "api-trace.json").write_text(
        json.dumps({"events": [{"stage": "request", "hash": "abc"}]}),
        encoding="utf-8",
    )

    packet = build_delta_audit_packet(run_dir, output_dir=tmp_path / "incomplete-api-packet")

    assert packet["action_required"] is True
    assert "api-trace missing digest-bound stage runtime" in packet["issues"]
    assert "api-trace missing digest-bound stage enforcement" in packet["issues"]
    assert "api-trace missing digest-bound stage response" in packet["issues"]


def test_verify_delta_audit_packet_detects_generated_packet_tamper(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-tamper"
    run_dir.mkdir()
    (run_dir / "api-trace.json").write_text(
        json.dumps({
            "events": [
                {"stage": "request", "hash": "r", "artifact_sha256": "a" * 64},
                {"stage": "runtime", "hash": "r", "artifact_sha256": "b" * 64},
                {"stage": "enforcement", "hash": "e", "artifact_sha256": "c" * 64},
                {"stage": "response", "hash": "s", "artifact_sha256": "d" * 64},
            ]
        }),
        encoding="utf-8",
    )
    out = tmp_path / "packet-tamper"
    build_delta_audit_packet(run_dir, output_dir=out)
    (out / "brief.md").write_text("tampered\n", encoding="utf-8")

    verification = verify_delta_audit_packet(out)

    assert verification["status"] == "invalid"
    assert "packet-file-hash-mismatch brief.md" in verification["issues"]
