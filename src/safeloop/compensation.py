"""Local-only compensation planner for external side effects.

SafeLoop never treats external side effects as exact rollback.  This module reads
run-local side-effects.jsonl and emits an operator-facing compensation plan only;
it does not call external services or execute compensation.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from safeloop.agent_watchdog import atomic_json
from safeloop.side_effect_ledger import read_side_effect_events

SCHEMA_VERSION = "compensation-plan.v1"
CAPABILITIES = {"none", "manual", "best_effort", "verified"}


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _action_ids_for_side_effect(run_dir: Path, event: dict[str, Any]) -> list[str]:
    explicit = event.get("action_id") or event.get("target", {}).get("action_id")
    if isinstance(explicit, str) and explicit:
        return [explicit]
    ids: list[str] = []
    path = run_dir / "action-events.jsonl"
    if not path.exists():
        return ids
    # If no explicit side-effect binding exists, a single completed action is the
    # only safe implicit binding; multiple actions remain unbound/manual.
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            action_id = json.loads(line).get("action_id")
        except json.JSONDecodeError:
            continue
        if isinstance(action_id, str) and action_id and action_id not in ids:
            ids.append(action_id)
    return ids if len(ids) == 1 else []


def _required_operator_action(capability: str, effect_class: str) -> str:
    if capability == "none":
        return "manual_review_required: no compensation capability is recorded; do not auto-compensate"
    if capability == "manual":
        return "manual_review_required: operator must perform and verify compensation outside SafeLoop"
    if capability == "best_effort":
        return "operator_review_required: best-effort compensation may mitigate but is not exact rollback"
    return "operator_verify_required: verified compensation may complete mitigation but is not exact rollback"


def _plan_item(run_dir: Path, event: dict[str, Any]) -> dict[str, Any]:
    comp = event.get("compensation") if isinstance(event.get("compensation"), dict) else {}
    capability = str(comp.get("capability") or "none")
    if capability not in CAPABILITIES:
        capability = "none"
    blockers: list[dict[str, str]] = []
    warnings: list[str] = ["external side effects are never exact rollback"]
    if capability == "none":
        blockers.append({"code": "no_compensation_capability", "message": "automatic compensation is blocked"})
    if capability == "manual":
        warnings.append("manual_review_required")
    if capability in {"best_effort", "verified"}:
        warnings.append("compensation is mitigation, not exact rollback")
    action_ids = _action_ids_for_side_effect(run_dir, event)
    item = {
        "side_effect_id": str(event.get("event_id") or event.get("side_effect_id") or "unknown"),
        "effect_class": str(event.get("effect_class") or "unknown"),
        "phase": str(event.get("phase") or "unknown"),
        "adapter": event.get("adapter") if isinstance(event.get("adapter"), dict) else {"name": str(event.get("adapter") or "unknown")},
        "external_ref": event.get("external_ref"),
        "compensation": {"capability": capability, **comp},
        "compensation_action_recorded": bool(comp.get("action")),
        "exact_rollback": False,
        "required_operator_action": _required_operator_action(capability, str(event.get("effect_class") or "unknown")),
        "blockers": blockers,
        "warnings": warnings,
    }
    if action_ids:
        item["action_id"] = action_ids[0]
        item["action_ids"] = action_ids
    return item


def build_compensation_plan(run_dir: str | Path, *, side_effect_id: str | None = None, action_id: str | None = None, dry_run: bool = True) -> dict[str, Any]:
    run_path = Path(run_dir)
    events = read_side_effect_events(run_path / "side-effects.jsonl")
    items = [_plan_item(run_path, event) for event in events]
    if side_effect_id:
        items = [item for item in items if item.get("side_effect_id") == side_effect_id]
    if action_id:
        items = [item for item in items if item.get("action_id") == action_id or action_id in item.get("action_ids", [])]
    blockers = [b for item in items for b in item.get("blockers", [])]
    warnings: list[str] = []
    if not items:
        warnings.append("manual_review_required: no matching side effects found")
    if any(item.get("compensation", {}).get("capability") == "manual" for item in items):
        warnings.append("manual_review_required")
    if any(item.get("compensation", {}).get("capability") in {"best_effort", "verified"} for item in items):
        warnings.append("operator_review_required: external compensation requires operator verification")
    status = "blocked" if blockers else "manual_review_required" if any("manual_review_required" in warning for warning in warnings) else "operator_review_required" if warnings else "ok"
    plan = {
        "schema_version": SCHEMA_VERSION,
        "run_id": _load_json(run_path / "run.json").get("run_id") if (run_path / "run.json").exists() else None,
        "mode": "dry-run" if dry_run else "plan",
        "side_effect_id": side_effect_id,
        "action_id": action_id,
        "status": status,
        "exact_rollback": False,
        "items": items,
        "blockers": blockers,
        "warnings": warnings,
        "external_execution": False,
        "network_calls": False,
    }
    atomic_json(run_path / "compensation-plan.json", plan)
    return plan


def compensation_section_for_rollback(run_dir: str | Path, *, action_id: str | None = None, include_compensation: bool = False) -> dict[str, Any]:
    if include_compensation:
        plan = build_compensation_plan(run_dir, action_id=action_id, dry_run=True)
        return {"included": True, "plan_path": str(Path(run_dir) / "compensation-plan.json"), **plan}
    return {"included": False, "warnings": ["manual_review_required: side effects require --include-compensation for a compensation plan"], "exact_rollback": False}
