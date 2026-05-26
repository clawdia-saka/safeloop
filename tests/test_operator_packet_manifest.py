from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from safeloop.operator_packet import write_operator_packet_v2
from safeloop.operator_packet_manifest import write_operator_packet_manifest, verify_operator_packet_manifest

ROOT = Path(__file__).resolve().parents[1]


def make_run_dir(tmp_path: Path) -> Path:
    run_dir = tmp_path / "run"
    (run_dir / "verification").mkdir(parents=True)
    (run_dir / "run.json").write_text(
        json.dumps({"run_id": "run-manifest", "task_id": "packet-manifest", "status": "completed"}, indent=2),
        encoding="utf-8",
    )
    (run_dir / "rollback-plan.json").write_text(
        json.dumps(
            {
                "schema_version": "rollback-plan.v1",
                "status": "planned",
                "run_id": "run-manifest",
                "checkpoint_id": "cp-0001",
                "covered_local_file_changes": {"modified": ["note.txt"], "created": [], "deleted": []},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (run_dir / "verification" / "verify-artifacts-result.json").write_text(
        json.dumps({"status": "valid", "issues": [], "warnings": []}, indent=2),
        encoding="utf-8",
    )
    return run_dir


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "safeloop.cli", *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )


def test_write_manifest_for_generated_operator_packet_and_verify_valid(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)
    packet = write_operator_packet_v2(run_dir)

    manifest = write_operator_packet_manifest(run_dir, packet)
    result = verify_operator_packet_manifest(run_dir)

    assert manifest["schema_version"] == "operator-packet-manifest.v1"
    assert manifest["packet_path"] == "operator-packet-v2.md"
    assert manifest["packet_sha256"].startswith("sha256:")
    assert manifest["run_id"] == "run-manifest"
    assert (run_dir / "operator-packet-manifest.json").exists()
    assert result["verification"]["status"] == "valid"
    assert result["verification"]["issues"] == []


def test_modifying_operator_packet_invalidates_manifest_verification(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)
    packet = write_operator_packet_v2(run_dir)
    write_operator_packet_manifest(run_dir, packet)

    packet.write_text(packet.read_text(encoding="utf-8") + "\nmutated\n", encoding="utf-8")

    result = verify_operator_packet_manifest(run_dir)

    assert result["verification"]["status"] == "invalid"
    assert any("packet_sha256 mismatch" in issue for issue in result["verification"]["issues"])


def test_modifying_source_artifact_invalidates_manifest_verification(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)
    packet = write_operator_packet_v2(run_dir)
    write_operator_packet_manifest(run_dir, packet)

    (run_dir / "rollback-plan.json").write_text(json.dumps({"status": "changed"}), encoding="utf-8")

    result = verify_operator_packet_manifest(run_dir)

    assert result["verification"]["status"] == "invalid"
    assert any("source artifact sha256 mismatch: rollback-plan.json" in issue for issue in result["verification"]["issues"])


def test_missing_optional_artifact_absent_at_generation_remains_valid(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)
    packet = write_operator_packet_v2(run_dir)
    manifest = write_operator_packet_manifest(run_dir, packet)

    absent = next(item for item in manifest["source_artifacts"] if item["path"] == "compensation-result.json")
    assert absent["present"] is False
    assert absent["required"] is False
    assert not (run_dir / "compensation-result.json").exists()

    result = verify_operator_packet_manifest(run_dir)

    assert result["verification"]["status"] == "valid"


def test_missing_artifact_present_at_generation_invalidates_manifest(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)
    (run_dir / "compensation-result.json").write_text(
        json.dumps({"schema_version": "compensation-result.v1", "run_id": "run-manifest", "status": "manual_review_required"}),
        encoding="utf-8",
    )
    packet = write_operator_packet_v2(run_dir)
    write_operator_packet_manifest(run_dir, packet)

    (run_dir / "compensation-result.json").unlink()

    result = verify_operator_packet_manifest(run_dir)

    assert result["verification"]["status"] == "invalid"
    assert any("source artifact missing: compensation-result.json" in issue for issue in result["verification"]["issues"])


def test_manifest_tracks_external_outbox_source_artifact(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)
    (run_dir / "external-outbox.json").write_text(
        json.dumps(
            {
                "schema_version": "external-outbox.v1",
                "run_id": "run-manifest",
                "items": [
                    {
                        "schema_version": "external-outbox-item.v1",
                        "outbox_id": "outbox-0001",
                        "phase": "pending",
                        "status": "pending",
                        "exact_rollback": False,
                        "dispatch_allowed": False,
                    }
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    packet = write_operator_packet_v2(run_dir)

    manifest = write_operator_packet_manifest(run_dir, packet)

    outbox = next(item for item in manifest["source_artifacts"] if item["path"] == "external-outbox.json")
    assert outbox["present"] is True
    assert outbox["required"] is False


def test_manifest_tracks_runtime_tool_firewall_source_artifact(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)
    (run_dir / "runtime-tool-firewall.jsonl").write_text(
        json.dumps(
            {
                "schema_version": "runtime-tool-firewall-route.v1",
                "event_id": "fw-0001",
                "run_id": "run-manifest",
                "route": "manual_review",
                "manual_review_required": True,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    packet = write_operator_packet_v2(run_dir)

    manifest = write_operator_packet_manifest(run_dir, packet)

    firewall = next(item for item in manifest["source_artifacts"] if item["path"] == "runtime-tool-firewall.jsonl")
    assert firewall["present"] is True
    assert firewall["required"] is False


def test_manifest_tracks_tool_shim_source_artifacts(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)
    shim_bin = run_dir / "tool-shims" / "bin"
    shim_bin.mkdir(parents=True)
    (run_dir / "tool-shims" / "tool-shims.json").write_text(
        json.dumps({"schema_version": "tool-shims.v1", "enabled": True}, indent=2),
        encoding="utf-8",
    )
    (shim_bin / "rm").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    packet = write_operator_packet_v2(run_dir)

    manifest = write_operator_packet_manifest(run_dir, packet)

    metadata = next(item for item in manifest["source_artifacts"] if item["path"] == "tool-shims/tool-shims.json")
    shim = next(item for item in manifest["source_artifacts"] if item["path"] == "tool-shims/bin/rm")
    assert metadata["present"] is True
    assert metadata["required"] is False
    assert shim["present"] is True
    assert shim["required"] is True


def test_manifest_boundary_is_local_tamper_evident_not_tamper_proof(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)
    packet = write_operator_packet_v2(run_dir)
    manifest = write_operator_packet_manifest(run_dir, packet)
    text = json.dumps(manifest, sort_keys=True).lower()

    assert manifest["boundary"] == {
        "exact_local_rollback_only": True,
        "external_exact_rollback": False,
        "external_compensation_manual_review_only": True,
        "runtime_unknown_tool_manual_review": True,
        "tamper_evident_local_only": True,
    }
    assert "tamper-proof" not in text
    assert "tamper proof" not in text


def test_manifest_missing_required_source_entry_is_invalid(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)
    packet = write_operator_packet_v2(run_dir)
    manifest = write_operator_packet_manifest(run_dir, packet)
    manifest["source_artifacts"] = [item for item in manifest["source_artifacts"] if item["path"] != "run.json"]
    (run_dir / "operator-packet-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    result = verify_operator_packet_manifest(run_dir)

    assert result["verification"]["status"] == "invalid"
    assert any("source artifact entry missing: run.json" in issue for issue in result["verification"]["issues"])


def test_optional_source_artifact_added_after_generation_invalidates_manifest(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)
    packet = write_operator_packet_v2(run_dir)
    write_operator_packet_manifest(run_dir, packet)

    (run_dir / "compensation-plan.json").write_text(json.dumps({"status": "new"}), encoding="utf-8")

    result = verify_operator_packet_manifest(run_dir)

    assert result["verification"]["status"] == "invalid"
    assert any("source artifact appeared after manifest generation: compensation-plan.json" in issue for issue in result["verification"]["issues"])


def test_manifest_boundary_mismatch_is_invalid(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)
    packet = write_operator_packet_v2(run_dir)
    manifest = write_operator_packet_manifest(run_dir, packet)
    manifest["boundary"]["external_exact_rollback"] = True
    (run_dir / "operator-packet-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    result = verify_operator_packet_manifest(run_dir)

    assert result["verification"]["status"] == "invalid"
    assert any("boundary mismatch" in issue for issue in result["verification"]["issues"])


def test_operator_packet_cli_write_manifest_and_verify(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)

    write = run_cli("operator-packet", str(run_dir), "--write-manifest")
    verify = run_cli("operator-packet-verify", str(run_dir))

    assert write.returncode == 0, write.stdout + write.stderr
    assert "Operator packet manifest written:" in write.stdout
    assert verify.returncode == 0, verify.stdout + verify.stderr
    assert "operator-packet verification: valid" in verify.stdout


def test_operator_packet_verify_accepts_custom_manifest_path(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)
    packet = write_operator_packet_v2(run_dir)
    custom_manifest = tmp_path / "custom-manifest.json"
    write_operator_packet_manifest(run_dir, packet, manifest_path=custom_manifest)

    result = run_cli("operator-packet-verify", str(run_dir), "--manifest", str(custom_manifest))

    assert result.returncode == 0, result.stdout + result.stderr
    assert "operator-packet verification: valid" in result.stdout


def test_operator_packet_verify_rejects_manifest_packet_path_traversal(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)
    packet = write_operator_packet_v2(run_dir)
    manifest = write_operator_packet_manifest(run_dir, packet)
    manifest["packet_path"] = "../outside.md"
    (run_dir / "operator-packet-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    result = verify_operator_packet_manifest(run_dir)

    assert result["verification"]["status"] == "invalid"
    assert any("artifact path must stay under run directory" in issue for issue in result["verification"]["issues"])


def test_operator_packet_verify_rejects_manifest_packet_path_absolute(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)
    packet = write_operator_packet_v2(run_dir)
    manifest = write_operator_packet_manifest(run_dir, packet)
    manifest["packet_path"] = str(tmp_path / "outside.md")
    (run_dir / "operator-packet-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    result = verify_operator_packet_manifest(run_dir)

    assert result["verification"]["status"] == "invalid"
    assert any("artifact path must stay under run directory" in issue for issue in result["verification"]["issues"])


def test_operator_packet_manifest_rejects_symlinked_source_artifact(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)
    outside = tmp_path / "outside-plan.json"
    outside.write_text(json.dumps({"status": "outside"}), encoding="utf-8")
    (run_dir / "rollback-plan.json").unlink()
    (run_dir / "rollback-plan.json").symlink_to(outside)
    packet = write_operator_packet_v2(run_dir)

    try:
        write_operator_packet_manifest(run_dir, packet)
    except ValueError as exc:
        assert "artifact path must not contain symlinks" in str(exc)
    else:
        raise AssertionError("symlinked source artifact should be rejected")


def test_operator_packet_manifest_rejects_output_outside_run_dir(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)
    packet = tmp_path / "outside-packet.md"
    packet.write_text("outside packet", encoding="utf-8")

    try:
        write_operator_packet_manifest(run_dir, packet)
    except ValueError as exc:
        assert "artifact path must stay under run directory" in str(exc)
    else:
        raise AssertionError("manifested packet output outside run_dir should be rejected")


def test_operator_packet_cli_write_manifest_rejects_output_outside_run_dir_before_writing(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)
    outside_packet = tmp_path / "outside-packet.md"

    result = run_cli("operator-packet", str(run_dir), "--output", str(outside_packet), "--write-manifest")

    assert result.returncode == 2
    assert "artifact path must stay under run directory" in result.stderr
    assert not outside_packet.exists()
