from __future__ import annotations

import json

from safeloop.cli import main
from safeloop.compensation import build_compensation_plan
from safeloop.external_effects import record_external_effect


def _run_dir(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "run.json").write_text(json.dumps({"run_id": "run-ext", "repo": str(tmp_path)}), encoding="utf-8")
    return run_dir


def test_builds_compensation_plan_from_external_effect_registry(tmp_path):
    run_dir = _run_dir(tmp_path)
    record_external_effect(
        run_dir,
        kind="webhook",
        target="https://example.test/hook/123",
        action="sent",
        evidence_path_or_url="logs/webhook.log",
        quote_or_field="delivery_id=abc123",
        compensation_capability="best_effort",
    )

    plan = build_compensation_plan(run_dir)

    assert plan["schema_version"] == "compensation-plan.v1"
    assert plan["run_id"] == "run-ext"
    assert plan["source"] == "external-effects.jsonl"
    assert plan["exact_rollback"] is False
    assert plan["external_execution"] is False
    assert plan["network_calls"] is False
    assert plan["status"] == "operator_review_required"
    assert len(plan["items"]) == 1
    item = plan["items"][0]
    assert item["external_effect_id"] == "ext-0001"
    assert item["effect_id"] == "ext-0001"
    assert "side_effect_id" not in item
    assert item["kind"] == "webhook"
    assert item["target"] == "https://example.test/hook/123"
    assert item["action"] == "sent"
    assert item["evidence_ref"] == "external-effects.jsonl#ext-0001"
    assert item["compensation"]["capability"] == "best_effort"
    assert item["exact_rollback"] is False
    assert "mitigation, not exact rollback" in " ".join(item["warnings"])
    assert json.loads((run_dir / "compensation-plan.json").read_text(encoding="utf-8")) == plan


def test_missing_external_evidence_blocks_plan_and_requires_manual_review(tmp_path):
    run_dir = _run_dir(tmp_path)
    (run_dir / "external-effects.jsonl").write_text(
        json.dumps(
            {
                "schema_version": "external-side-effect.v1",
                "effect_id": "ext-no-evidence",
                "run_id": "run-ext",
                "kind": "message",
                "target": "telegram:ops",
                "action": "sent",
                "created_at": "2026-05-14T00:00:00+00:00",
                "exact_rollback": False,
                "compensation_capability": "verified",
                "evidence": {"path": "", "quote_or_field": ""},
                "status": "recorded",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    plan = build_compensation_plan(run_dir)

    assert plan["status"] == "blocked"
    assert plan["exact_rollback"] is False
    assert plan["items"] == []
    assert {blocker["code"] for blocker in plan["blockers"]} == {"invalid_external_effect_registry"}
    assert "manual_review_required" in " ".join(plan["warnings"])


def test_invalid_external_registry_exact_rollback_true_blocks_compensation_plan(tmp_path):
    run_dir = _run_dir(tmp_path)
    (run_dir / "external-effects.jsonl").write_text(
        json.dumps(
            {
                "schema_version": "external-side-effect.v1",
                "effect_id": "ext-overclaim",
                "run_id": "run-ext",
                "kind": "webhook",
                "target": "https://example.test/hook/invalid",
                "action": "sent",
                "created_at": "2026-05-14T00:00:00+00:00",
                "exact_rollback": True,
                "compensation_capability": "manual",
                "evidence": {"path": "logs/webhook.log", "quote_or_field": "delivery_id=bad"},
                "status": "manual_review_required",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    plan = build_compensation_plan(run_dir)

    assert plan["status"] == "blocked"
    assert plan["source"] == "external-effects.jsonl"
    assert plan["exact_rollback"] is False
    assert plan["items"] == []
    assert plan["warnings"] == ["manual_review_required: invalid external-effect registry blocks compensation planning"]
    assert plan["blockers"] == [
        {
            "code": "invalid_external_effect_registry",
            "message": "external-effects.jsonl line 1: exact_rollback must always be false for external side effects",
        }
    ]
    written = json.loads((run_dir / "compensation-plan.json").read_text(encoding="utf-8"))
    assert written == plan


def test_compensate_cli_prefers_external_effect_registry_when_present(tmp_path):
    run_dir = _run_dir(tmp_path)
    record_external_effect(
        run_dir,
        kind="github_issue",
        target="owner/repo#1",
        action="commented",
        evidence_path_or_url="https://github.example/comment/1",
        quote_or_field="comment_id=1",
        compensation_capability="manual",
    )

    rc = main(["compensate", str(run_dir), "--json"])

    assert rc == 0
    plan = json.loads((run_dir / "compensation-plan.json").read_text(encoding="utf-8"))
    assert plan["source"] == "external-effects.jsonl"
    assert plan["items"][0]["external_effect_id"] == "ext-0001"
    assert plan["items"][0]["exact_rollback"] is False
