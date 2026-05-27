from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def make_run_dir(tmp_path: Path) -> Path:
    run_dir = tmp_path / "run"
    (run_dir / "verification").mkdir(parents=True)
    (run_dir / "run.json").write_text(
        json.dumps(
            {
                "run_id": "run-cli",
                "task_id": "operator-packet-cli-demo",
                "status": "completed",
                "started_at": "2026-05-13T00:00:00+00:00",
                "ended_at": "2026-05-13T00:00:02+00:00",
                "latest_event_hash": "sha256:abc123",
                "checkpoint_count": 1,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (run_dir / "verification" / "verify-artifacts-result.json").write_text(
        json.dumps({"status": "valid", "issues": [], "warnings": [], "latest_event_hash": "sha256:abc123"}, indent=2),
        encoding="utf-8",
    )
    (run_dir / "rollback-plan.json").write_text(
        json.dumps(
            {
                "schema_version": "rollback-plan.v1",
                "status": "planned",
                "run_id": "run-cli",
                "checkpoint_id": "cp-0001",
                "covered_local_file_changes": {"modified": ["service.md"], "created": [], "deleted": []},
            },
            indent=2,
        ),
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


def test_operator_packet_cli_writes_default_v2_packet(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)
    result = run_cli("operator-packet", str(run_dir))
    assert result.returncode == 0, result.stdout + result.stderr
    out = run_dir / "operator-packet-v2.md"
    assert out.exists()
    assert f"Operator packet v2 written: {out}" in result.stdout
    assert "recommended next action: rollback_available" in result.stdout


def test_operator_packet_cli_writes_custom_output_path(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)
    custom = tmp_path / "custom-packet.md"
    result = run_cli("operator-packet", str(run_dir), "--v2", "--output", str(custom))
    assert result.returncode == 0, result.stdout + result.stderr
    assert custom.exists()
    assert f"Operator packet v2 written: {custom}" in result.stdout


def test_operator_packet_cli_external_evidence_appears_in_compensation_and_manual_review_tables(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)
    evidence = tmp_path / "fake-external.log"
    evidence.write_text("fake-ticket created; exact_rollback=false\n", encoding="utf-8")

    result = run_cli(
        "operator-packet",
        str(run_dir),
        "--external-evidence",
        str(evidence),
        "--compensation-adapter",
        "manual",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    packet = (run_dir / "operator-packet-v2.md").read_text(encoding="utf-8")
    assert str(evidence) in packet
    assert "## 5. Compensation decision table" in packet
    assert "## 6. Manual review queue" in packet
    assert "| {} | manual | manual | false |".format(evidence) in packet
    assert "recommended next action: compensation_review_required" in result.stdout


def test_operator_packet_cli_generated_packet_contains_required_sections_and_boundaries(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)
    result = run_cli("operator-packet", str(run_dir))
    assert result.returncode == 0, result.stdout + result.stderr
    packet = (run_dir / "operator-packet-v2.md").read_text(encoding="utf-8")
    for required in [
        "## 1. Run summary",
        "## 2. Artifact verification",
        "firewall policy profile: strict-local",
        "tool-shims: disabled",
        "tool-shim coverage: disabled",
        "## 4. Rollback decision table",
        "## 5. Compensation decision table",
        "## 6. Manual review queue",
        "## 8. Boundary statement",
        "Exact rollback only applies to covered local file changes.",
        "External side effects are manual-review/compensation only.",
        "SafeLoop does not claim exact rollback for actions outside the local repo.",
    ]:
        assert required in packet
    assert "external_side_effect |" not in packet or "| false |" in packet


def test_operator_packet_cli_reports_invalid_run_dir(tmp_path: Path) -> None:
    missing = tmp_path / "missing-run"
    missing.mkdir()
    result = run_cli("operator-packet", str(missing))
    assert result.returncode != 0
    assert "missing run.json" in result.stderr
