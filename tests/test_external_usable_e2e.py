from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "safeloop.cli", *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )


def make_run_dir(tmp_path: Path) -> Path:
    run_dir = tmp_path / "run-external-usable"
    run_dir.mkdir()
    (run_dir / "run.json").write_text(
        json.dumps(
            {
                "run_id": "run-external-usable-e2e",
                "task_id": "external-usable-e2e-review-harness",
                "status": "completed",
                "started_at": "2026-05-14T00:00:00+00:00",
                "ended_at": "2026-05-14T00:00:01+00:00",
                "latest_event_hash": "sha256:external-usable-e2e",
                "checkpoint_count": 1,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (run_dir / "rollback-plan.json").write_text(
        json.dumps(
            {
                "schema_version": "rollback-plan.v1",
                "status": "planned",
                "run_id": "run-external-usable-e2e",
                "checkpoint_id": "cp-0001",
                "covered_local_file_changes": {
                    "modified": ["src/service.py"],
                    "created": [],
                    "deleted": [],
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (run_dir / "verification").mkdir()
    (run_dir / "verification" / "verify-artifacts-result.json").write_text(
        json.dumps({"status": "valid", "issues": [], "warnings": []}, indent=2),
        encoding="utf-8",
    )
    return run_dir


def test_external_usable_cli_to_operator_packet_separates_rollback_from_compensation(tmp_path: Path) -> None:
    """#109 current path: record an external effect, then render operator-packet.

    This harness is intentionally deterministic and offline. It exercises the
    external-usable seam without inventing a live third-party integration.
    """

    run_dir = make_run_dir(tmp_path)

    record = run_cli(
        "external",
        "record",
        str(run_dir),
        "--kind",
        "webhook",
        "--target",
        "https://example.test/hooks/safeloop-review",
        "--action",
        "sent",
        "--evidence",
        "logs/webhook-delivery.log",
        "--quote-or-field",
        "delivery_id=deterministic-e2e-001",
        "--capability",
        "manual",
    )

    assert record.returncode == 0, record.stdout + record.stderr
    assert "External side effect recorded: ext-0001" in record.stdout
    assert "exact rollback: false" in record.stdout
    assert "status: manual_review_required" in record.stdout

    packet_cli = run_cli("operator-packet", str(run_dir))

    assert packet_cli.returncode == 0, packet_cli.stdout + packet_cli.stderr
    assert "recommended next action: compensation_review_required" in packet_cli.stdout

    packet = (run_dir / "operator-packet-v2.md").read_text(encoding="utf-8")
    effect_rows = [line for line in packet.splitlines() if "webhook:https://example.test/hooks/safeloop-review" in line]

    assert "| src/service.py | local_file | src/service.py | rollback_available | true | rollback-plan.json |" in packet
    assert "| cp-0001 | action_group | cp-0001 | rollback_available | true | rollback-plan.json |" in packet
    assert any("| external_side_effect |" in line and "manual_review_required | false | external-effects.jsonl |" in line for line in effect_rows)
    assert any("| compensation_item |" in line and "compensation_review_required | false | external-effects.jsonl |" in line for line in effect_rows)
    assert "Review and compensate manually; do not treat local rollback as external rollback." in packet
    assert "Exact rollback only applies to covered local file changes." in packet
    assert "SafeLoop does not claim exact rollback for actions outside the local repo." in packet


@pytest.mark.xfail(reason="Future #111-#115 integration placeholder; keep non-blocking until artifacts exist", strict=False)
def test_future_external_usable_artifacts_are_linked_when_implemented(tmp_path: Path) -> None:
    """Placeholder gates for future slices, not a failing requirement today.

    Expected later artifacts: compensation plan/result (#111/#112), external
    adapter fake/demo contract (#113/#114), and packet integration (#115).
    """

    run_dir = make_run_dir(tmp_path)
    for artifact in [
        "compensation-plan.json",
        "compensation-result.json",
        "fake-webhook-demo.json",
        "adapter-contract.json",
    ]:
        assert (run_dir / artifact).exists()
