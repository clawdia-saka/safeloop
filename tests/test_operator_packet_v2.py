from __future__ import annotations

import json
import subprocess
from pathlib import Path

from safeloop.operator_packet import render_operator_packet_v2, write_operator_packet_v2

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "docs" / "specs" / "operator-packet-v2.md"
EXAMPLE = ROOT / "examples" / "operator_packet_v2.md"
FULL_DEMO = ROOT / "examples" / "full_demo.sh"


def make_run_dir(tmp_path: Path) -> Path:
    run_dir = tmp_path / "run"
    (run_dir / "verification").mkdir(parents=True)
    (run_dir / "checkpoints" / "cp-0001").mkdir(parents=True)
    (run_dir / "run.json").write_text(
        json.dumps(
            {
                "run_id": "run-demo",
                "task_id": "operator-packet-v2-demo",
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
        json.dumps(
            {
                "status": "valid",
                "issues": [],
                "warnings": ["demo warning"],
                "latest_event_hash": "sha256:abc123",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (run_dir / "rollback-plan.json").write_text(
        json.dumps(
            {
                "status": "planned",
                "run_id": "run-demo",
                "checkpoint_id": "cp-0001",
                "files": {"modified": ["service.md"], "created": [], "deleted": []},
                "commands": [
                    "python -m safeloop.cli rollback apply /tmp/run run-demo cp-0001 --files service.md"
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (run_dir / "external-evidence.log").write_text(
        "fake-ticket: created outside repo; external_review_required; exact_rollback=false\n",
        encoding="utf-8",
    )
    return run_dir


def test_packet_v2_includes_required_decision_sections(tmp_path: Path) -> None:
    packet = render_operator_packet_v2(
        make_run_dir(tmp_path),
        external_evidence=["external-evidence.log"],
        compensation_adapter="manual_ticket_adapter",
    )

    for required in [
        "# SafeLoop Operator Packet v2",
        "## 1. Run summary",
        "run_id: run-demo",
        "task_id: operator-packet-v2-demo",
        "verification status: valid",
        "## 2. Artifact verification",
        "verify-artifacts status: valid",
        "local anchor status:",
        "evidence packet status:",
        "## 3. Change summary",
        "| Item | Type | Path / Ref | Status | Exact rollback | Evidence |",
        "## 4. Rollback decision table",
        "| Selection | Scope | Rollback status | Exact rollback | Blockers | Suggested command |",
        "all covered local files",
        "selected file rollback",
        "selected hunk rollback",
        "selected action group rollback",
        "## 5. Compensation decision table",
        "| Side effect | Adapter | Compensation capability | Exact rollback | Required action | Evidence |",
        "manual_ticket_adapter",
        "## 6. Manual review queue",
        "| Item | Reason | Risk | Recommended operator action |",
        "## 7. Recommended next action",
        "compensation_review_required",
        "## 8. Boundary statement",
    ]:
        assert required in packet


def test_packet_v2_boundary_never_claims_external_exact_rollback(tmp_path: Path) -> None:
    packet = render_operator_packet_v2(make_run_dir(tmp_path), external_evidence=["external-evidence.log"])

    assert "Exact rollback only applies to covered local file changes." in packet
    assert "External side effects are manual-review/compensation only." in packet
    assert "SafeLoop does not claim exact rollback for actions outside the local repo." in packet
    assert "| external-evidence.log | external_side_effect | external-evidence.log | manual_review_required | false |" in packet
    assert "| external-evidence.log | manual | manual | false | Review and compensate manually; do not treat local rollback as external rollback. |" in packet
    assert "external_side_effect | external-evidence.log | manual_review_required | true" not in packet


def test_operator_packet_for_recorded_unsafe_outbox_does_not_offer_resume_or_approve_as_normal_path(
    tmp_path: Path,
) -> None:
    run_dir = make_run_dir(tmp_path)
    run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    run.update({"status": "applied", "approval_policy": "unsafe_allow_without_hooks"})
    (run_dir / "run.json").write_text(json.dumps(run, indent=2), encoding="utf-8")
    (run_dir / "external-outbox.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "id": "outbox-1",
                        "checkpoint_id": "cp-0001",
                        "payload": {"kind": "webhook", "url": "https://example.invalid/hook"},
                        "status": "pending",
                    }
                ]
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    packet = render_operator_packet_v2(run_dir)

    assert "demo-only unsafe exception" in packet
    assert "Do not approve, resume, or dispatch external side effects from this outbox item" in packet
    assert "pending shadow review only" in packet
    assert "pending_unbound_external_outbox" in packet
    assert "safeloop approve" not in packet
    assert "safeloop resume" not in packet


def test_external_outbox_items_require_approval_request_digest_and_decision_binding(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)
    run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    run.update({"approval_policy": "unsafe_allow_without_hooks"})
    (run_dir / "run.json").write_text(json.dumps(run, indent=2), encoding="utf-8")
    (run_dir / "external-outbox.json").write_text(
        json.dumps(
            {
                "items": [
                    {"id": "outbox-1", "checkpoint_id": "cp-0001", "payload": {}, "status": "pending"},
                    {
                        "id": "outbox-2",
                        "checkpoint_id": "cp-0001",
                        "payload": {},
                        "status": "pending",
                        "approval_request_digest": "sha256:approval",
                        "approval_status": "approved",
                        "decision_id": "decision-1",
                        "dispatch_allowed": True,
                    },
                ]
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    packet = render_operator_packet_v2(run_dir)

    assert "approval_request_digest" in packet
    assert "approval_status" in packet
    assert "decision_id or waiver_id" in packet
    assert "dispatch_allowed: false unless lifecycle-bound" in packet
    assert "outbox-1" in packet
    assert "lifecycle_bound=false" in packet
    assert "outbox-2" in packet
    assert "lifecycle_bound=true" in packet


def test_write_operator_packet_v2_is_stable(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)
    first = write_operator_packet_v2(run_dir, output_path=run_dir / "operator-packet-v2.md")
    first_text = first.read_text(encoding="utf-8")
    second = write_operator_packet_v2(run_dir, output_path=run_dir / "operator-packet-v2.md")
    assert second.read_text(encoding="utf-8") == first_text


def test_packet_v2_reads_legacy_covered_local_file_changes_plan_shape(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)
    plan = json.loads((run_dir / "rollback-plan.json").read_text(encoding="utf-8"))
    plan["covered_local_file_changes"] = plan.pop("files")
    (run_dir / "rollback-plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")

    packet = render_operator_packet_v2(run_dir)

    assert "| service.md | local_file | service.md | rollback_available | true | rollback-plan.json |" in packet
    assert "--files service.md" in packet


def test_packet_v2_surfaces_external_registry_compensation_plan_and_result(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)
    (run_dir / "external-effects.jsonl").write_text(
        json.dumps(
            {
                "schema_version": "external-side-effect.v1",
                "effect_id": "ext-0001",
                "run_id": "run-demo",
                "kind": "github_issue",
                "target": "owner/repo#115",
                "action": "comment_created",
                "created_at": "2026-05-14T00:00:00+00:00",
                "exact_rollback": False,
                "compensation_capability": "best_effort",
                "status": "compensation_planned",
                "evidence": {"path": "external-receipts/github-comment.json", "quote_or_field": "comment_url"},
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "compensation-plan.json").write_text(
        json.dumps(
            {
                "status": "planned",
                "artifact_path": "compensation-plan.json",
                "items": [
                    {
                        "effect_id": "ext-0001",
                        "status": "planned",
                        "planned_action": "delete github issue comment if authorized",
                        "artifact_path": "external-receipts/github-comment.json",
                    }
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (run_dir / "compensation-result.json").write_text(
        json.dumps(
            {
                "status": "verified",
                "receipt": "external-receipts/github-delete-receipt.json",
                "items": [
                    {
                        "effect_id": "ext-0001",
                        "status": "verified",
                        "receipt_path": "external-receipts/github-delete-receipt.json",
                    }
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    packet = render_operator_packet_v2(run_dir)

    assert "## 5. External compensation / manual review status" in packet
    assert "external-effects.jsonl: present" in packet
    assert "compensation-plan.json: present (status: planned)" in packet
    assert "compensation-result.json: present (status: verified)" in packet
    assert "| ext-0001 | github_issue | owner/repo#115 | verified | false | external-effects.jsonl; compensation-result.json; receipt: external-receipts/github-delete-receipt.json |" in packet
    assert "| github_issue:owner/repo#115 | manual | best_effort | false | planned: delete github issue comment if authorized; result: verified | external-effects.jsonl; compensation-plan.json; compensation-result.json; receipt: external-receipts/github-delete-receipt.json |" in packet
    assert "local rollback cannot prove the external action was undone" in packet


def test_operator_packet_change_summary_reflects_valid_compensation_result_status(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)
    (run_dir / "external-effects.jsonl").write_text(
        json.dumps(
            {
                "schema_version": "external-side-effect.v1",
                "effect_id": "ext-0001",
                "run_id": "run-demo",
                "kind": "webhook",
                "target": "https://example.test/hook/123",
                "action": "sent",
                "created_at": "2026-05-14T00:00:00+00:00",
                "exact_rollback": False,
                "compensation_capability": "manual",
                "status": "manual_review_required",
                "evidence": {"path": "logs/webhook-send.log", "quote_or_field": "delivery_id"},
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "compensation-result.json").write_text(
        json.dumps(
            {
                "schema_version": "compensation-result.v1",
                "run_id": "run-demo",
                "effect_id": "ext-0001",
                "status": "compensation_completed",
                "operator": "ops",
                "exact_rollback": False,
                "manual_operator_result": True,
                "external_execution_by_safeloop": False,
                "local_rollback_applied": False,
                "evidence": {"path": "receipts/webhook-void.json", "quote_or_field": "receipt_id=void-123"},
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    packet = render_operator_packet_v2(run_dir)

    assert "compensation-result.json: present (status: compensation_completed)" in packet
    assert "| ext-0001 | webhook | https://example.test/hook/123 | compensation_completed | false | external-effects.jsonl; compensation-result.json; receipt: receipts/webhook-void.json |" in packet
    assert "| ext-0001 | webhook | https://example.test/hook/123 | manual_review_required | false | external-effects.jsonl |" not in packet
    assert "Exact rollback | Evidence" in packet
    assert "compensation_completed | true" not in packet


def test_operator_packet_completed_compensation_does_not_recommend_review_required(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)
    (run_dir / "external-effects.jsonl").write_text(
        json.dumps(
            {
                "schema_version": "external-side-effect.v1",
                "effect_id": "ext-0001",
                "run_id": "run-demo",
                "kind": "webhook",
                "target": "https://example.test/hook/123",
                "action": "sent",
                "created_at": "2026-05-14T00:00:00+00:00",
                "exact_rollback": False,
                "compensation_capability": "manual",
                "status": "manual_review_required",
                "evidence": {"path": "logs/webhook-send.log", "quote_or_field": "delivery_id"},
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "compensation-result.json").write_text(
        json.dumps(
            {
                "schema_version": "compensation-result.v1",
                "run_id": "run-demo",
                "effect_id": "ext-0001",
                "status": "compensation_completed",
                "operator": "ops",
                "exact_rollback": False,
                "manual_operator_result": True,
                "external_execution_by_safeloop": False,
                "local_rollback_applied": False,
                "evidence": {"path": "receipts/webhook-void.json", "quote_or_field": "receipt_id=void-123"},
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    packet = render_operator_packet_v2(run_dir)

    assert "| ext-0001 | webhook | https://example.test/hook/123 | compensation_completed | false |" in packet
    assert "recommended next action: compensation_complete_verify_receipt" in packet
    assert "recommended next action: compensation_review_required" not in packet


def test_operator_packet_requires_receipt_for_compensation_completed(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)
    (run_dir / "external-effects.jsonl").write_text(
        json.dumps(
            {
                "schema_version": "external-side-effect.v1",
                "effect_id": "ext-0001",
                "run_id": "run-demo",
                "kind": "webhook",
                "target": "https://example.test/hook/123",
                "action": "sent",
                "created_at": "2026-05-14T00:00:00+00:00",
                "exact_rollback": False,
                "compensation_capability": "manual",
                "status": "compensation_completed",
                "evidence": {"path": "logs/webhook-send.log", "quote_or_field": "delivery_id"},
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "compensation-result.json").write_text(
        json.dumps(
            {
                "schema_version": "compensation-result.v1",
                "run_id": "run-demo",
                "effect_id": "ext-0001",
                "status": "compensation_completed",
                "operator": "ops",
                "exact_rollback": False,
                "manual_operator_result": True,
                "external_execution_by_safeloop": False,
                "local_rollback_applied": False,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    packet = render_operator_packet_v2(run_dir)

    assert "manual_review_required: missing compensation receipt" in packet
    assert "compensation-result.json: invalid (manual_review_required: missing compensation receipt)" in packet
    assert "| ext-0001 | webhook | https://example.test/hook/123 | manual_review_required: missing compensation receipt | false | external-effects.jsonl |" in packet
    assert "result: compensation_completed" not in packet
    assert "completed | external-effects.jsonl; compensation-result.json" not in packet
    assert "recommended next action: blocked" in packet


def test_operator_packet_flags_side_effect_ledger_without_external_effects_registry(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)
    (run_dir / "side-effects.jsonl").write_text(
        json.dumps(
            {
                "schema_version": "side-effect-ledger.v1",
                "event_id": "sevt-0001",
                "run_id": "run-demo",
                "created_at": "2026-05-14T00:00:00+00:00",
                "phase": "observed",
                "effect_class": "http",
                "adapter": {"name": "fake-http", "version": "test", "supports_idempotency": False},
                "target": {"url": "https://example.test/webhook"},
                "idempotency_key": None,
                "external_ref": "evt_123",
                "privacy": {"redaction": "strict", "contains_secret": False, "raw_payload_persisted": False},
                "compensation": {"capability": "manual"},
                "reason": "integration emitted legacy ledger event",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    packet = render_operator_packet_v2(run_dir)

    assert "external-effects.jsonl: not_present" in packet
    assert "side-effects.jsonl: present" in packet
    assert "side-effect-ledger.v1" in packet
    assert "sevt-0001" in packet
    assert "evt_123" in packet
    assert "manual_review_required" in packet
    assert "recommended next action: compensation_review_required" in packet
    assert "recommended next action: verify_only" not in packet
    assert "no external side effects recorded" not in packet
    assert "| sevt-0001 | http | evt_123 | manual_review_required | false | side-effects.jsonl |" in packet


def test_operator_packet_v2_accepts_legacy_side_effect_compensation_result_receipt(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)
    legacy_effect_id = "sevt-d252edc5a23c48789ae34be2a0017638"
    (run_dir / "side-effects.jsonl").write_text(
        json.dumps(
            {
                "schema_version": "side-effect-ledger.v1",
                "event_id": legacy_effect_id,
                "run_id": "run-demo",
                "created_at": "2026-05-14T00:00:00+00:00",
                "phase": "observed",
                "effect_class": "http",
                "external_ref": "evt_legacy_receipt",
                "compensation": {"capability": "manual"},
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "compensation-plan.json").write_text(
        json.dumps(
            {
                "schema_version": "compensation-plan.v1",
                "status": "planned",
                "items": [
                    {
                        "side_effect_id": legacy_effect_id,
                        "planned_action": "verify the remote HTTP event was compensated by operator",
                        "capability": "manual",
                    }
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (run_dir / "compensation-result.json").write_text(
        json.dumps(
            {
                "schema_version": "compensation-result.v1",
                "effect_id": legacy_effect_id,
                "status": "completed",
                "operator": "tt",
                "evidence": {
                    "path": "receipts/legacy-http-compensation.md",
                    "quote_or_field": "operator confirmed external event compensation",
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    packet = render_operator_packet_v2(run_dir)

    assert "side-effects.jsonl: present" in packet
    assert legacy_effect_id in packet
    assert "evt_legacy_receipt" in packet
    assert "result: completed" in packet
    assert "receipt: receipts/legacy-http-compensation.md" in packet
    assert "recommended next action: compensation_complete_verify_receipt" in packet
    assert "recommended next action: compensation_review_required" not in packet
    assert "missing compensation receipt" not in packet
    assert (
        f"| {legacy_effect_id} | http | evt_legacy_receipt | completed | false | "
        "side-effects.jsonl; compensation-result.json; receipt: receipts/legacy-http-compensation.md |"
    ) in packet



def test_operator_packet_v2_correlates_legacy_side_effect_id_alias_receipt(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)
    event_id = "legacy-event-outer-id"
    side_effect_id = "sevt-alias-side-effect-id"
    (run_dir / "side-effects.jsonl").write_text(
        json.dumps(
            {
                "schema_version": "side-effect-ledger.v1",
                "event_id": event_id,
                "side_effect_id": side_effect_id,
                "run_id": "run-demo",
                "phase": "observed",
                "effect_class": "chat",
                "external_ref": "chat-msg-123",
                "compensation": {"capability": "manual"},
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "compensation-plan.json").write_text(
        json.dumps(
            {
                "schema_version": "compensation-plan.v1",
                "status": "planned",
                "items": [
                    {
                        "side_effect_id": side_effect_id,
                        "planned_action": "verify chat deletion receipt",
                        "capability": "manual",
                    }
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (run_dir / "compensation-result.json").write_text(
        json.dumps(
            {
                "schema_version": "compensation-result.v1",
                "effect_id": side_effect_id,
                "status": "verified",
                "evidence": {
                    "path": "receipts/chat-delete.md",
                    "quote_or_field": "message deletion receipt id",
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    packet = render_operator_packet_v2(run_dir)

    assert event_id in packet
    assert side_effect_id in packet
    assert "chat-msg-123" in packet
    assert "result: verified" in packet
    assert "receipt: receipts/chat-delete.md" in packet
    assert "recommended next action: compensation_complete_verify_receipt" in packet
    assert "recommended next action: compensation_review_required" not in packet
    assert "Exact rollback only applies to covered local file changes." in packet


def test_operator_packet_does_not_fallback_to_legacy_ledger_when_external_registry_file_exists(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)
    (run_dir / "external-effects.jsonl").write_text("", encoding="utf-8")
    (run_dir / "side-effects.jsonl").write_text(
        json.dumps(
            {
                "schema_version": "side-effect-ledger.v1",
                "event_id": "sevt-0001",
                "run_id": "run-demo",
                "phase": "observed",
                "effect_class": "http",
                "external_ref": "evt_123",
                "compensation": {"capability": "manual"},
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    packet = render_operator_packet_v2(run_dir)

    assert "external-effects.jsonl: present" in packet
    assert "legacy side-effect ledger compatibility: not_applicable" in packet
    assert "sevt-0001" not in packet
    assert "recommended next action: verify_only" not in packet  # local files still make rollback available
    assert "recommended next action: rollback_available" in packet


def test_operator_packet_blocks_invalid_side_effect_ledger_without_saying_none_recorded(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)
    (run_dir / "side-effects.jsonl").write_text("{not-json}\n", encoding="utf-8")

    packet = render_operator_packet_v2(run_dir)

    assert "side-effects.jsonl: invalid" in packet
    assert "invalid_side_effect_ledger" in packet
    assert "recommended next action: blocked" in packet
    assert "no external side effects recorded" not in packet
    assert "block packet use until legacy side-effect ledger is corrected" in packet


def test_packet_v2_spec_and_example_document_required_boundaries() -> None:
    spec = SPEC.read_text(encoding="utf-8")
    example = EXAMPLE.read_text(encoding="utf-8")
    for text in [spec, example]:
        assert "# SafeLoop Operator Packet v2" in text
        assert "Compensation capability enum" in text
        assert "Exact rollback only applies to covered local file changes." in text
        assert "External side effects are manual-review/compensation only." in text
        assert "SafeLoop does not claim exact rollback for actions outside the local repo." in text
        assert "hosted systems" in text or "third-party services" in text


def test_full_demo_produces_operator_packet_v2(tmp_path: Path) -> None:
    env = {**dict(), **__import__("os").environ}
    env["SAFELOOP_FULL_DEMO_ROOT"] = str(tmp_path / "full-demo")
    result = subprocess.run(
        ["bash", str(FULL_DEMO)],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    run_line = next(line for line in result.stdout.splitlines() if line.startswith("Operator packet v2: "))
    packet_path = Path(run_line.removeprefix("Operator packet v2: "))
    packet = packet_path.read_text(encoding="utf-8")
    assert "# SafeLoop Operator Packet v2" in packet
    assert "## 4. Rollback decision table" in packet
    assert "## 5. Compensation decision table" in packet
    assert "External side effects are manual-review/compensation only." in packet
