from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from safeloop.compensation import (
    CompensationResultValidationError,
    create_compensation_result,
    validate_compensation_result_record,
)
from safeloop.external_effects import record_external_effect
ROOT = Path(__file__).resolve().parents[1]


def make_run_dir(tmp_path: Path) -> Path:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "run.json").write_text(
        json.dumps(
            {
                "run_id": "run-external",
                "task_id": "external-effect-demo",
                "status": "completed",
                "started_at": "2026-05-14T00:00:00+00:00",
                "ended_at": "2026-05-14T00:00:01+00:00",
                "latest_event_hash": "sha256:external",
                "checkpoint_count": 1,
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


def record_effect(run_dir: Path) -> dict:
    return record_external_effect(
        run_dir,
        kind="webhook",
        target="https://example.test/hook/123",
        action="sent",
        evidence_path_or_url="logs/webhook-send.log",
        quote_or_field="delivery_id=abc123",
        compensation_capability="manual",
    )


def test_creates_manual_compensation_result_receipt_linked_to_external_effect(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)
    effect = record_effect(run_dir)

    result = create_compensation_result(
        run_dir,
        effect_id=effect["effect_id"],
        status="compensation_completed",
        operator="ops@example.test",
        evidence_path_or_url="logs/manual-compensation.log",
        quote_or_field="void_confirmation=ok-789",
        notes="Operator voided the downstream delivery.",
    )

    assert result["schema_version"] == "compensation-result.v1"
    assert result["run_id"] == "run-external"
    assert result["effect_id"] == "ext-0001"
    assert result["status"] == "compensation_completed"
    assert result["operator"] == "ops@example.test"
    assert result["exact_rollback"] is False
    assert result["manual_operator_result"] is True
    assert result["external_execution_by_safeloop"] is False
    assert result["evidence"] == {"path": "logs/manual-compensation.log", "quote_or_field": "void_confirmation=ok-789"}
    assert "covered_local_file_changes" not in result
    assert "file_counts" not in result
    assert (run_dir / "compensation-result.json").exists()
    assert json.loads((run_dir / "compensation-result.json").read_text(encoding="utf-8")) == result


@pytest.mark.parametrize("status", ["compensation_completed", "compensation_failed", "ignored_by_operator", "manual_review_required"])
def test_compensation_result_allows_manual_statuses(tmp_path: Path, status: str) -> None:
    run_dir = make_run_dir(tmp_path)
    effect = record_effect(run_dir)

    result = create_compensation_result(
        run_dir,
        effect_id=effect["effect_id"],
        status=status,
        operator="ops",
        evidence_path_or_url="https://evidence.example.test/ticket/1",
        quote_or_field="ticket.status=closed",
    )

    assert result["status"] == status
    assert result["evidence"]["url"] == "https://evidence.example.test/ticket/1"


def test_compensation_result_requires_evidence_path_or_url_and_quote(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)
    effect = record_effect(run_dir)

    with pytest.raises(CompensationResultValidationError, match="evidence path or url is required"):
        create_compensation_result(
            run_dir,
            effect_id=effect["effect_id"],
            status="compensation_completed",
            operator="ops",
            evidence_path_or_url="",
            quote_or_field="ticket.status=closed",
        )

    with pytest.raises(CompensationResultValidationError, match="evidence quote_or_field is required"):
        create_compensation_result(
            run_dir,
            effect_id=effect["effect_id"],
            status="compensation_completed",
            operator="ops",
            evidence_path_or_url="ticket://1",
            quote_or_field="",
        )

    assert not (run_dir / "compensation-result.json").exists()


def test_compensation_result_rejects_unknown_effect_id(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)
    record_effect(run_dir)

    with pytest.raises(CompensationResultValidationError, match="effect_id not found"):
        create_compensation_result(
            run_dir,
            effect_id="ext-missing",
            status="ignored_by_operator",
            operator="ops",
            evidence_path_or_url="ticket://1",
            quote_or_field="decision=ignore",
        )


def test_validate_compensation_result_record_requires_receipt_for_completed() -> None:
    errors = validate_compensation_result_record(
        {
            "schema_version": "compensation-result.v1",
            "effect_id": "ext-0001",
            "status": "compensation_completed",
            "exact_rollback": False,
            "local_rollback_applied": False,
        },
        known_effect_ids={"ext-0001"},
    )

    assert "compensation-result.json: manual_review_required: missing compensation receipt" in errors

    assert validate_compensation_result_record(
        {
            "schema_version": "compensation-result.v1",
            "effect_id": "ext-0001",
            "status": "compensation_completed",
            "exact_rollback": False,
            "local_rollback_applied": False,
            "evidence": {"path": "receipts/manual.json", "quote_or_field": "receipt_id"},
        },
        known_effect_ids={"ext-0001"},
    ) == []


def test_compensation_result_cli_writes_receipt_without_local_rollback_claim(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)
    effect = record_effect(run_dir)

    completed = run_cli(
        "compensate",
        "result",
        str(run_dir),
        "--effect-id",
        effect["effect_id"],
        "--status",
        "compensation_failed",
        "--operator",
        "ops",
        "--evidence",
        "ticket://1",
        "--quote-or-field",
        "status=failed",
        "--json",
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    receipt = json.loads(completed.stdout)
    assert receipt["schema_version"] == "compensation-result.v1"
    assert receipt["effect_id"] == effect["effect_id"]
    assert receipt["status"] == "compensation_failed"
    assert receipt["exact_rollback"] is False
    assert receipt["local_rollback_applied"] is False
    assert "covered_local_file_changes" not in receipt
