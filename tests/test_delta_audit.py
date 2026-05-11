import hashlib
import json
from pathlib import Path

from safeloop.delta_audit import build_delta_audit_packet, verify_delta_audit_packet


def chained_event(event: dict, previous_hash: str | None = None) -> dict:
    event = {**event, "prev_event_hash": previous_hash}
    event["event_hash"] = hashlib.sha256(json.dumps(event, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return event


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
    side_effect = chained_event({"phase": "prepared", "privacy": {"redaction": "strict"}, "kind": "file_write"})
    (run_dir / "side-effects.jsonl").write_text(
        json.dumps(side_effect) + "\n",
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
        "side-effects.jsonl",
        "side-effects-ledger.json",
        "pr-lifecycle.json",
    ]
    assert {item["kind"] for item in manifest["source_evidence"]} == {
        "api_trace",
        "side_effects",
        "side_effects_ledger",
        "pr_lifecycle",
    }
    assert bundle["bound_evidence"] == manifest["source_evidence"]
    assert bundle["artifacts"]["api_trace"]["summary"]["event_count"] == 4
    assert bundle["artifacts"]["side_effects"]["summary"]["line_count"] == 1
    assert bundle["artifacts"]["side_effects_ledger"]["summary"]["top_level_keys"] == ["effects"]
    assert bundle["artifacts"]["pr_lifecycle"]["summary"]["top_level_keys"] == ["pr", "state"]
    assert all("payload" not in artifact for artifact in bundle["artifacts"].values())
    assert "api-trace.json" in brief
    assert "side-effects.jsonl" in brief
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
    (run_dir / "side-effects.jsonl").write_text(json.dumps({"effects": []}) + "\n", encoding="utf-8")

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



def test_delta_audit_packet_can_opt_in_to_payloads_for_local_debugging(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-payload"
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

    packet = build_delta_audit_packet(run_dir, output_dir=tmp_path / "payload-packet", include_payload=True)

    assert packet["action_required"] is False
    bundle = read_json(tmp_path / "payload-packet" / "evidence-bundle.json")
    assert bundle["artifacts"]["api_trace"]["payload"]["events"][0]["stage"] == "request"


def test_delta_audit_packet_rejects_sensitive_api_trace_payload_even_when_not_embedded(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-sensitive"
    run_dir.mkdir()
    (run_dir / "api-trace.json").write_text(
        json.dumps({
            "events": [
                {"stage": "request", "hash": "r", "artifact_sha256": "a" * 64, "headers": {"authorization": "Bearer secret"}},
                {"stage": "runtime", "hash": "r", "artifact_sha256": "b" * 64},
                {"stage": "enforcement", "hash": "e", "artifact_sha256": "c" * 64},
                {"stage": "response", "hash": "s", "artifact_sha256": "d" * 64},
            ]
        }),
        encoding="utf-8",
    )

    packet = build_delta_audit_packet(run_dir, output_dir=tmp_path / "sensitive-packet")

    assert packet["action_required"] is True
    assert "api-trace.json.events[0].headers contains sensitive key authorization" in packet["issues"]
    bundle = read_json(tmp_path / "sensitive-packet" / "evidence-bundle.json")
    assert "payload" not in bundle["artifacts"]["api_trace"]


def test_delta_audit_packet_refuses_sensitive_payload_even_when_payload_opted_in(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-sensitive-opt-in"
    run_dir.mkdir()
    (run_dir / "api-trace.json").write_text(
        json.dumps({
            "events": [
                {"stage": "request", "hash": "r", "artifact_sha256": "a" * 64, "headers": {"authorization": "Bearer secret"}},
                {"stage": "runtime", "hash": "r", "artifact_sha256": "b" * 64},
                {"stage": "enforcement", "hash": "e", "artifact_sha256": "c" * 64},
                {"stage": "response", "hash": "s", "artifact_sha256": "d" * 64},
            ]
        }),
        encoding="utf-8",
    )

    packet = build_delta_audit_packet(run_dir, output_dir=tmp_path / "sensitive-opt-in-packet", include_payload=True)

    assert packet["action_required"] is True
    assert "api-trace.json.events[0].headers contains sensitive key authorization" in packet["issues"]
    bundle = read_json(tmp_path / "sensitive-opt-in-packet" / "evidence-bundle.json")
    assert bundle["payload_included"] is False
    assert "payload" not in bundle["artifacts"]["api_trace"]


def test_delta_audit_packet_detects_camel_case_sensitive_keys(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-camel-sensitive"
    run_dir.mkdir()
    (run_dir / "api-trace.json").write_text(
        json.dumps({
            "events": [
                {"stage": "request", "hash": "r", "artifact_sha256": "a" * 64, "accessToken": "secret"},
                {"stage": "runtime", "hash": "r", "artifact_sha256": "b" * 64},
                {"stage": "enforcement", "hash": "e", "artifact_sha256": "c" * 64, "privateKey": "secret"},
                {"stage": "response", "hash": "s", "artifact_sha256": "d" * 64},
            ]
        }),
        encoding="utf-8",
    )

    packet = build_delta_audit_packet(run_dir, output_dir=tmp_path / "camel-sensitive-packet")

    assert packet["action_required"] is True
    assert "api-trace.json.events[0] contains sensitive key accessToken" in packet["issues"]
    assert "api-trace.json.events[2] contains sensitive key privateKey" in packet["issues"]


def test_delta_audit_packet_validates_jsonl_side_effect_evidence(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-jsonl"
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
    (run_dir / "side-effects.jsonl").write_text('{"phase": "prepared"}\nnot-json\n', encoding="utf-8")

    packet = build_delta_audit_packet(run_dir, output_dir=tmp_path / "jsonl-packet")

    assert packet["action_required"] is True
    assert "malformed jsonl evidence side-effects.jsonl line 2" in packet["issues"]


def test_delta_audit_packet_scans_side_effect_jsonl_for_sensitive_keys(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-side-sensitive"
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
    (run_dir / "side-effects.jsonl").write_text(
        json.dumps({"phase": "prepared", "authorization": "Bearer secret"}) + "\n",
        encoding="utf-8",
    )

    packet = build_delta_audit_packet(run_dir, output_dir=tmp_path / "side-sensitive-packet")

    assert packet["action_required"] is True
    assert "side-effects.jsonl[0] contains sensitive key authorization" in packet["issues"]


def test_delta_audit_packet_validates_side_effect_hash_chain_when_present(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-side-chain"
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
    (run_dir / "side-effects.jsonl").write_text(
        json.dumps({"phase": "prepared", "prev_event_hash": None, "event_hash": "0" * 64}) + "\n"
        + json.dumps({"phase": "committed", "prev_event_hash": "1" * 64, "event_hash": "2" * 64}) + "\n",
        encoding="utf-8",
    )

    packet = build_delta_audit_packet(run_dir, output_dir=tmp_path / "side-chain-packet")

    assert packet["action_required"] is True
    assert "side-effects.jsonl hash mismatch line 1" in packet["issues"]
    assert "side-effects.jsonl prev hash mismatch line 2" in packet["issues"]

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
