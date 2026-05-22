from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from safeloop.external_outbox import (
    ExternalOutboxError,
    commit_external_outbox_item,
    enqueue_external_outbox_item,
    prepare_external_outbox_item,
    read_external_outbox,
)
from safeloop.operator_packet import render_operator_packet_v2

ROOT = Path(__file__).resolve().parents[1]


def make_run_dir(tmp_path: Path) -> Path:
    run_dir = tmp_path / "run"
    (run_dir / "verification").mkdir(parents=True)
    (run_dir / "run.json").write_text(
        json.dumps(
            {
                "run_id": "run-outbox",
                "task_id": "external-outbox-v3",
                "status": "completed",
                "started_at": "2026-05-22T00:00:00+00:00",
                "ended_at": "2026-05-22T00:00:01+00:00",
                "latest_event_hash": "sha256:outbox",
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
                "run_id": "run-outbox",
                "checkpoint_id": "cp-0001",
                "covered_local_file_changes": {"modified": ["service.md"], "created": [], "deleted": []},
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


def test_enqueue_external_outbox_item_records_pending_not_dispatched(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)

    item = enqueue_external_outbox_item(
        run_dir,
        kind="webhook",
        target="https://example.test/hooks/review",
        action="send",
        evidence_path_or_url="outbox/webhook-intent.json",
        quote_or_field="payload_hash=sha256:intent",
        compensation_capability="manual",
        reason="operator requested webhook after local rollback",
        actor="codex",
    )

    assert item["schema_version"] == "external-outbox-item.v1"
    assert item["outbox_id"] == "outbox-0001"
    assert item["phase"] == "pending"
    assert item["status"] == "pending"
    assert item["dispatch_allowed"] is False
    assert item["exact_rollback"] is False
    assert item["compensation_boundary"] == "compensatable_not_reversible"
    assert item["compensation_status"] == "not_started"
    assert not (run_dir / "external-effects.jsonl").exists()

    outbox = read_external_outbox(run_dir)
    assert outbox["schema_version"] == "external-outbox.v1"
    assert outbox["run_id"] == "run-outbox"
    assert outbox["counts"] == {"pending": 1, "prepared": 0, "committed": 0, "compensated": 0, "manual_review": 0}


def test_prepare_requires_lifecycle_binding_before_dispatch(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)
    item = enqueue_external_outbox_item(
        run_dir,
        kind="message",
        target="ops-channel",
        action="post",
        evidence_path_or_url="outbox/message-intent.json",
        quote_or_field="message_template=rollback-warning",
        reason="notify operator",
    )

    with pytest.raises(ExternalOutboxError, match="approval_request_digest"):
        prepare_external_outbox_item(run_dir, item["outbox_id"], approval_request_digest="", approval_status="approved", decision_id="decision-1")
    with pytest.raises(ExternalOutboxError, match="decision_id or waiver_id"):
        prepare_external_outbox_item(
            run_dir,
            item["outbox_id"],
            approval_request_digest="sha256:approval",
            approval_status="approved",
        )

    prepared = prepare_external_outbox_item(
        run_dir,
        item["outbox_id"],
        approval_request_digest="sha256:approval",
        approval_status="approved",
        decision_id="decision-1",
        actor="operator",
    )

    assert prepared["phase"] == "prepared"
    assert prepared["status"] == "prepared"
    assert prepared["dispatch_allowed"] is True
    assert prepared["approval_request_digest"] == "sha256:approval"
    assert prepared["decision_id"] == "decision-1"


def test_commit_requires_prepared_outbox_and_records_external_effect_boundary(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)
    item = enqueue_external_outbox_item(
        run_dir,
        kind="webhook",
        target="https://example.test/hooks/review",
        action="send",
        evidence_path_or_url="outbox/webhook-intent.json",
        quote_or_field="payload_hash=sha256:intent",
        compensation_capability="best_effort",
        reason="send review webhook",
    )

    with pytest.raises(ExternalOutboxError, match="prepared"):
        commit_external_outbox_item(
            run_dir,
            item["outbox_id"],
            external_ref="delivery_id=abc123",
            evidence_path_or_url="logs/webhook-delivery.log",
            quote_or_field="delivery_id=abc123",
        )

    prepare_external_outbox_item(
        run_dir,
        item["outbox_id"],
        approval_request_digest="sha256:approval",
        approval_status="approved",
        decision_id="decision-1",
    )
    result = commit_external_outbox_item(
        run_dir,
        item["outbox_id"],
        external_ref="delivery_id=abc123",
        evidence_path_or_url="logs/webhook-delivery.log",
        quote_or_field="delivery_id=abc123",
        actor="adapter/fake-webhook",
    )

    committed = result["item"]
    effect = result["external_effect"]
    assert committed["phase"] == "committed"
    assert committed["status"] == "committed"
    assert committed["external_effect_id"] == "ext-0001"
    assert committed["external_ref"] == "delivery_id=abc123"
    assert committed["compensation_status"] == "manual_review_required"
    assert effect["effect_id"] == "ext-0001"
    assert effect["outbox_id"] == item["outbox_id"]
    assert effect["lifecycle_phase"] == "committed"
    assert effect["exact_rollback"] is False
    assert effect["status"] == "manual_review_required"


def test_external_outbox_cli_covers_enqueue_prepare_commit_list(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)

    enqueue = run_cli(
        "external",
        "outbox",
        "enqueue",
        str(run_dir),
        "--kind",
        "webhook",
        "--target",
        "https://example.test/hooks/review",
        "--action",
        "send",
        "--evidence",
        "outbox/webhook-intent.json",
        "--quote-or-field",
        "payload_hash=sha256:intent",
        "--reason",
        "send review webhook",
        "--capability",
        "manual",
    )
    assert enqueue.returncode == 0, enqueue.stdout + enqueue.stderr
    assert "External outbox item enqueued: outbox-0001" in enqueue.stdout
    assert "phase: pending" in enqueue.stdout

    prepare = run_cli(
        "external",
        "outbox",
        "prepare",
        str(run_dir),
        "outbox-0001",
        "--approval-request-digest",
        "sha256:approval",
        "--approval-status",
        "approved",
        "--decision-id",
        "decision-1",
    )
    assert prepare.returncode == 0, prepare.stdout + prepare.stderr
    assert "phase: prepared" in prepare.stdout

    commit = run_cli(
        "external",
        "outbox",
        "commit",
        str(run_dir),
        "outbox-0001",
        "--external-ref",
        "delivery_id=abc123",
        "--evidence",
        "logs/webhook-delivery.log",
        "--quote-or-field",
        "delivery_id=abc123",
    )
    assert commit.returncode == 0, commit.stdout + commit.stderr
    assert "phase: committed" in commit.stdout
    assert "external effect: ext-0001" in commit.stdout

    listed = run_cli("external", "outbox", "list", str(run_dir), "--json")

    assert listed.returncode == 0, listed.stdout + listed.stderr
    outbox = json.loads(listed.stdout)
    assert outbox["counts"]["committed"] == 1
    assert outbox["items"][0]["external_effect_id"] == "ext-0001"


def test_operator_packet_surfaces_external_outbox_lifecycle_separately(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)
    item = enqueue_external_outbox_item(
        run_dir,
        kind="webhook",
        target="https://example.test/hooks/review",
        action="send",
        evidence_path_or_url="outbox/webhook-intent.json",
        quote_or_field="payload_hash=sha256:intent",
        compensation_capability="manual",
        reason="send review webhook",
    )

    packet = render_operator_packet_v2(run_dir)

    assert "external-outbox.json: present" in packet
    assert "| outbox-0001 | external_outbox | webhook:https://example.test/hooks/review | pending | false | external-outbox.json |" in packet
    assert "recommended next action: external_outbox_review_required" in packet

    prepare_external_outbox_item(
        run_dir,
        item["outbox_id"],
        approval_request_digest="sha256:approval",
        approval_status="approved",
        decision_id="decision-1",
    )
    commit_external_outbox_item(
        run_dir,
        item["outbox_id"],
        external_ref="delivery_id=abc123",
        evidence_path_or_url="logs/webhook-delivery.log",
        quote_or_field="delivery_id=abc123",
    )

    committed_packet = render_operator_packet_v2(run_dir)

    assert "| outbox-0001 | external_outbox | webhook:https://example.test/hooks/review | committed | false | external-outbox.json; external-effects.jsonl |" in committed_packet
    assert "| ext-0001 | webhook | https://example.test/hooks/review | manual_review_required | false | external-effects.jsonl |" in committed_packet
    assert "recommended next action: compensation_review_required" in committed_packet


def test_external_outbox_docs_pin_lifecycle_boundary() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    spec = (ROOT / "docs" / "specs" / "external-outbox-v1.md").read_text(encoding="utf-8")
    compensation = (ROOT / "docs" / "compensation.md").read_text(encoding="utf-8")

    for text in [readme, spec, compensation]:
        assert "external-outbox.json" in text
        assert "pending" in text
        assert "prepared" in text
        assert "committed" in text
        assert "exact_rollback" in text
    assert "SafeLoop does not perform network calls" in spec
