from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["python", "-m", "safeloop.cli", *args],
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


def test_external_usable_artifacts_link_effect_plan_result_packet_and_manifest(tmp_path: Path) -> None:
    """#136: #111-#115 artifacts are now concrete, linked, and verifiable.

    This remains local-only. It proves the external usable path connects the
    external-effect registry, compensation plan, compensation result receipt,
    operator packet rows, and operator packet manifest verification without
    promoting external side effects to exact rollback.
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

    plan_cli = run_cli("compensate", str(run_dir), "--json")
    assert plan_cli.returncode == 0, plan_cli.stdout + plan_cli.stderr
    plan = json.loads(plan_cli.stdout)

    result_cli = run_cli(
        "compensate",
        "result",
        str(run_dir),
        "--effect-id",
        "ext-0001",
        "--status",
        "compensation_completed",
        "--operator",
        "ops@example.test",
        "--evidence",
        "receipts/webhook-compensation.json",
        "--quote-or-field",
        "receipt_id=receipt-001",
        "--notes",
        "Operator verified the downstream webhook mitigation.",
        "--json",
    )
    assert result_cli.returncode == 0, result_cli.stdout + result_cli.stderr
    result = json.loads(result_cli.stdout)

    packet_cli = run_cli("operator-packet", str(run_dir), "--write-manifest")
    assert packet_cli.returncode == 0, packet_cli.stdout + packet_cli.stderr
    assert "manifest status: valid" in packet_cli.stdout

    verify_cli = run_cli("operator-packet-verify", str(run_dir))
    assert verify_cli.returncode == 0, verify_cli.stdout + verify_cli.stderr
    assert "operator-packet verification: valid" in verify_cli.stdout

    external_effects = [json.loads(line) for line in (run_dir / "external-effects.jsonl").read_text(encoding="utf-8").splitlines()]
    packet = (run_dir / "operator-packet-v2.md").read_text(encoding="utf-8")
    manifest = json.loads((run_dir / "operator-packet-manifest.json").read_text(encoding="utf-8"))

    assert len(external_effects) == 1
    external_effect = external_effects[0]
    assert external_effect["schema_version"] == "external-side-effect.v1"
    assert external_effect["effect_id"] == "ext-0001"
    assert external_effect["run_id"] == "run-external-usable-e2e"
    assert external_effect["kind"] == "webhook"
    assert external_effect["target"] == "https://example.test/hooks/safeloop-review"
    assert external_effect["action"] == "sent"
    assert external_effect["status"] == "manual_review_required"
    assert external_effect["exact_rollback"] is False
    assert external_effect["compensation_capability"] == "manual"
    assert external_effect["evidence"] == {"path": "logs/webhook-delivery.log", "quote_or_field": "delivery_id=deterministic-e2e-001"}
    assert "created_at" in external_effect
    assert plan["schema_version"] == "compensation-plan.v1"
    assert plan["source"] == "external-effects.jsonl"
    assert plan["exact_rollback"] is False
    assert plan["external_execution"] is False
    assert plan["network_calls"] is False
    assert plan["items"][0]["effect_id"] == "ext-0001"
    assert plan["items"][0]["evidence_ref"] == "external-effects.jsonl#ext-0001"
    assert plan["items"][0]["exact_rollback"] is False

    assert result["schema_version"] == "compensation-result.v1"
    assert result["effect_id"] == "ext-0001"
    assert result["status"] == "compensation_completed"
    assert result["manual_operator_result"] is True
    assert result["exact_rollback"] is False
    assert result["local_rollback_applied"] is False
    assert result["external_execution_by_safeloop"] is False
    assert result["evidence"] == {"path": "receipts/webhook-compensation.json", "quote_or_field": "receipt_id=receipt-001"}

    receipt_ref = "external-effects.jsonl; compensation-result.json; receipt: receipts/webhook-compensation.json"
    item_ref = "webhook:https://example.test/hooks/safeloop-review"
    assert f"| ext-0001 | webhook | https://example.test/hooks/safeloop-review | compensation_completed | false | {receipt_ref} |" in packet
    assert f"| {item_ref} | external_side_effect | {item_ref} | compensation_completed | false | {receipt_ref} |" in packet
    assert f"| {item_ref} | manual_review_item | {item_ref} | compensation_completed | false | {receipt_ref} |" in packet
    assert f"| {item_ref} | compensation_item | {item_ref} | compensation_completed | false | {receipt_ref} |" in packet
    assert f"| {item_ref} | manual | manual | false | planned: sent; result: compensation_completed | external-effects.jsonl; compensation-plan.json; compensation-result.json; receipt: receipts/webhook-compensation.json |" in packet
    assert "recommended next action: compensation_complete_verify_receipt" in packet
    assert "Exact rollback only applies to covered local file changes." in packet
    assert "SafeLoop does not claim exact rollback for actions outside the local repo." in packet

    assert manifest["verification"]["status"] == "valid"
    source_paths = {source["path"] for source in manifest["source_artifacts"]}
    assert {"external-effects.jsonl", "compensation-plan.json", "compensation-result.json"}.issubset(source_paths)
