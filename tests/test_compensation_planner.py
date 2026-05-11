from __future__ import annotations

import json

from safeloop.cli import main
from safeloop.compensation import build_compensation_plan
from safeloop.side_effect_ledger import LocalSideEffectLedger, SideEffectAdapterIdentity, SideEffectRecord


def _run_dir(tmp_path):
    d = tmp_path / "run"
    d.mkdir()
    (d / "run.json").write_text(json.dumps({"run_id": "run-1", "repo": str(tmp_path)}), encoding="utf-8")
    (d / "timeline.jsonl").write_text("", encoding="utf-8")
    return d


def _append(run_dir, capability: str, *, event_id: str | None = None, action_id: str | None = None, phase: str = "committed") -> str:
    ledger = LocalSideEffectLedger(run_dir / "side-effects.jsonl", "run-1")
    event = ledger.append(
        SideEffectRecord(
            phase=phase,
            effect_class="chat",
            adapter=SideEffectAdapterIdentity("local/demo", "test", supports_idempotency=True),
            target={"channel": "demo", **({"action_id": action_id} if action_id else {})},
            reason="test",
            idempotency_key="idem-1",
            external_ref="demo-ref",
            compensation={"capability": capability},
        )
    )
    if event_id:
        lines = [json.loads(line) for line in (run_dir / "side-effects.jsonl").read_text(encoding="utf-8").splitlines()]
        lines[-1]["event_id"] = event_id
        (run_dir / "side-effects.jsonl").write_text("\n".join(json.dumps(x, sort_keys=True, separators=(",", ":")) for x in lines) + "\n", encoding="utf-8")
        return event_id
    return event["event_id"]


def test_manual_side_effect_requires_manual_review(tmp_path):
    run_dir = _run_dir(tmp_path)
    sid = _append(run_dir, "manual", event_id="sevt-0001")
    plan = build_compensation_plan(run_dir, side_effect_id=sid)
    item = plan["items"][0]
    assert plan["schema_version"] == "compensation-plan.v1"
    assert item["side_effect_id"] == "sevt-0001"
    assert item["compensation"]["capability"] == "manual"
    assert item["exact_rollback"] is False
    assert "manual_review_required" in " ".join(item["warnings"] + plan["warnings"])


def test_none_blocks_automatic_compensation(tmp_path):
    run_dir = _run_dir(tmp_path)
    _append(run_dir, "none")
    plan = build_compensation_plan(run_dir)
    assert plan["status"] == "blocked"
    assert plan["items"][0]["blockers"][0]["code"] == "no_compensation_capability"


def test_best_effort_and_verified_are_never_exact_rollback(tmp_path):
    run_dir = _run_dir(tmp_path)
    _append(run_dir, "best_effort")
    _append(run_dir, "verified")
    plan = build_compensation_plan(run_dir)
    assert plan["exact_rollback"] is False
    assert [item["compensation"]["capability"] for item in plan["items"]] == ["best_effort", "verified"]
    assert all(item["exact_rollback"] is False for item in plan["items"])


def test_selected_action_plan_includes_related_side_effects(tmp_path, monkeypatch):
    run_dir = _run_dir(tmp_path)
    _append(run_dir, "best_effort", action_id="act-0003")
    (run_dir / "action-events.jsonl").write_text(json.dumps({"action_id": "act-0003", "name": "demo external action"}) + "\n", encoding="utf-8")
    import safeloop.cli as cli

    def fake_rollback_to_start(run_dir_arg, run_id, apply=False):
        artifact = {"schema_version": "rollback-plan.v1", "operation": "rollback_to_start", "run_id": run_id, "mode": "dry-run", "status": "ok", "preflight": {"status": "pass"}, "file_counts": {"created": 0, "modified": 0, "deleted": 0}, "external_side_effects": {"exact_rollback": False, "classification": "manual_review"}}
        cli.atomic_json(run_dir_arg / "rollback-plan.json", artifact)
        return artifact

    monkeypatch.setattr(cli, "rollback_to_start", fake_rollback_to_start)
    rc = main(["rollback", "plan", str(run_dir), "run-1", "--to-start", "--action", "act-0003", "--include-compensation"])
    assert rc == 0
    plan = json.loads((run_dir / "rollback-plan.json").read_text(encoding="utf-8"))
    comp = plan["compensation"]
    assert comp["included"] is True
    assert comp["items"][0]["action_id"] == "act-0003"
    assert comp["items"][0]["exact_rollback"] is False


def test_no_external_exact_rollback_true(tmp_path):
    run_dir = _run_dir(tmp_path)
    for cap in ["none", "manual", "best_effort", "verified"]:
        _append(run_dir, cap)
    plan = build_compensation_plan(run_dir)
    values = [plan["exact_rollback"], *[i["exact_rollback"] for i in plan["items"]]]
    assert "true" not in json.dumps({"exact_rollback_values": values})
