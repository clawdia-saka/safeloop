from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from safeloop.external_effects import ExternalEffectValidationError, record_external_effect
from safeloop.operator_packet import render_operator_packet_v2

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
    (run_dir / "rollback-plan.json").write_text(
        json.dumps(
            {
                "schema_version": "rollback-plan.v1",
                "status": "planned",
                "run_id": "run-external",
                "checkpoint_id": "cp-0001",
                "covered_local_file_changes": {"modified": ["service.md"], "created": [], "deleted": []},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return run_dir


def read_effects(run_dir: Path) -> list[dict]:
    return [json.loads(line) for line in (run_dir / "external-effects.jsonl").read_text(encoding="utf-8").splitlines()]


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["python", "-m", "safeloop.cli", *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )


def test_records_external_effect_with_exact_rollback_false(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)

    effect = record_external_effect(
        run_dir,
        kind="webhook",
        target="https://example.test/hook/123",
        action="sent",
        evidence_path_or_url="logs/webhook.log",
        quote_or_field="delivery_id=abc123",
        compensation_capability="manual",
    )

    assert effect["schema_version"] == "external-side-effect.v1"
    assert effect["effect_id"] == "ext-0001"
    assert effect["run_id"] == "run-external"
    assert effect["exact_rollback"] is False
    assert effect["compensation_capability"] == "manual"
    assert effect["status"] == "manual_review_required"
    assert effect["evidence"] == {"path": "logs/webhook.log", "quote_or_field": "delivery_id=abc123"}
    assert read_effects(run_dir) == [effect]


def test_rejects_exact_rollback_true_for_external_effects(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)

    with pytest.raises(ExternalEffectValidationError, match="exact_rollback must always be false"):
        record_external_effect(
            run_dir,
            kind="webhook",
            target="https://example.test/hook/123",
            action="sent",
            evidence_path_or_url="logs/webhook.log",
            quote_or_field="delivery_id=abc123",
            exact_rollback=True,
        )

    assert not (run_dir / "external-effects.jsonl").exists()


def test_missing_evidence_fails_validation(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)

    with pytest.raises(ExternalEffectValidationError, match="evidence"):
        record_external_effect(
            run_dir,
            kind="message",
            target="telegram:ops",
            action="sent",
            evidence_path_or_url="",
            quote_or_field="message_id=42",
        )

    with pytest.raises(ExternalEffectValidationError, match="quote_or_field"):
        record_external_effect(
            run_dir,
            kind="message",
            target="telegram:ops",
            action="sent",
            evidence_path_or_url="https://example.test/evidence/42",
            quote_or_field="",
        )


def test_multiple_effects_get_deterministic_ids(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)

    first = record_external_effect(
        run_dir,
        kind="github_issue",
        target="clawdia-saka/safeloop#1",
        action="commented",
        evidence_path_or_url="https://github.com/clawdia-saka/safeloop/issues/1#issuecomment-1",
        quote_or_field="comment_id=1",
    )
    second = record_external_effect(
        run_dir,
        kind="email",
        target="ops@example.test",
        action="sent",
        evidence_path_or_url="mailbox://sent/2",
        quote_or_field="message_id=2",
    )

    assert [first["effect_id"], second["effect_id"]] == ["ext-0001", "ext-0002"]
    assert [e["effect_id"] for e in read_effects(run_dir)] == ["ext-0001", "ext-0002"]


def test_rejects_raw_sensitive_payload_storage(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)

    with pytest.raises(ExternalEffectValidationError, match="raw sensitive payload"):
        record_external_effect(
            run_dir,
            kind="webhook",
            target="https://example.test/hook",
            action="sent",
            evidence_path_or_url="logs/webhook.log",
            quote_or_field="authorization: bearer secret-token",
        )


def test_external_effect_is_not_represented_as_covered_local_file_rollback(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)
    record_external_effect(
        run_dir,
        kind="webhook",
        target="https://example.test/hook/123",
        action="sent",
        evidence_path_or_url="logs/webhook.log",
        quote_or_field="delivery_id=abc123",
    )

    packet = render_operator_packet_v2(run_dir)

    assert "external-effects.jsonl" in packet
    assert "webhook:https://example.test/hook/123" in packet
    assert "webhook:https://example.test/hook/123 | local_file" not in packet
    assert "webhook:https://example.test/hook/123 | external_side_effect" in packet
    assert "webhook:https://example.test/hook/123 | external_side_effect | webhook:https://example.test/hook/123 | manual_review_required | false" in packet


def test_external_record_cli_writes_jsonl_and_prints_boundary(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)

    result = run_cli(
        "external",
        "record",
        str(run_dir),
        "--kind",
        "webhook",
        "--target",
        "https://example.test/hook/123",
        "--action",
        "sent",
        "--evidence",
        "logs/webhook.log",
        "--quote-or-field",
        "delivery_id=abc123",
        "--capability",
        "manual",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "External side effect recorded: ext-0001" in result.stdout
    assert "exact rollback: false" in result.stdout
    assert "status: manual_review_required" in result.stdout
    effects = read_effects(run_dir)
    assert effects[0]["kind"] == "webhook"
    assert effects[0]["target"] == "https://example.test/hook/123"


def test_operator_packet_generation_still_works_with_external_effect_registry(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)
    record_external_effect(
        run_dir,
        kind="webhook",
        target="https://example.test/hook/123",
        action="sent",
        evidence_path_or_url="logs/webhook.log",
        quote_or_field="delivery_id=abc123",
    )

    result = run_cli("operator-packet", str(run_dir))

    assert result.returncode == 0, result.stdout + result.stderr
    packet = (run_dir / "operator-packet-v2.md").read_text(encoding="utf-8")
    assert "external-effects.jsonl" in packet
    assert "recommended next action: compensation_review_required" in result.stdout
