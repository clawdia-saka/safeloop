"""Local-only compensation planner for external side effects.

SafeLoop never treats external side effects as exact rollback.  This module reads
run-local side-effects.jsonl and emits an operator-facing compensation plan only;
it does not call external services or execute compensation.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from safeloop.agent_watchdog import atomic_json
from safeloop.external_effects import read_external_effects
from safeloop.side_effect_ledger import read_side_effect_events

SCHEMA_VERSION = "compensation-plan.v1"
RESULT_SCHEMA_VERSION = "compensation-result.v1"
CAPABILITIES = {"none", "manual", "best_effort", "verified"}
RESULT_STATUSES = {"compensation_completed", "compensation_failed", "ignored_by_operator", "manual_review_required"}


class CompensationResultValidationError(ValueError):
    """Raised when a manual compensation result receipt is invalid."""


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


def _has_external_evidence(effect: dict[str, Any]) -> bool:
    evidence = effect.get("evidence")
    if not isinstance(evidence, dict):
        return False
    has_ref = bool(str(evidence.get("path") or evidence.get("url") or "").strip())
    has_quote = bool(str(evidence.get("quote_or_field") or "").strip())
    return has_ref and has_quote


def _external_plan_item(effect: dict[str, Any]) -> dict[str, Any]:
    effect_id = str(effect.get("effect_id") or "unknown")
    capability = str(effect.get("compensation_capability") or "none")
    if capability not in CAPABILITIES:
        capability = "none"

    blockers: list[dict[str, str]] = []
    warnings: list[str] = ["external side effects are never exact rollback"]
    if capability == "none":
        blockers.append({"code": "no_compensation_capability", "message": "automatic compensation is blocked"})
    if not _has_external_evidence(effect):
        blockers.append({"code": "missing_external_evidence", "message": "external evidence is required before planning compensation"})
        warnings.append("manual_review_required: missing external evidence blocks compensation planning")
    if capability == "manual":
        warnings.append("manual_review_required")
    if capability in {"best_effort", "verified"}:
        warnings.append("compensation is mitigation, not exact rollback")

    return {
        "external_effect_id": effect_id,
        "effect_id": effect_id,
        "kind": str(effect.get("kind") or "unknown"),
        "target": str(effect.get("target") or "unknown"),
        "action": str(effect.get("action") or "unknown"),
        "status": str(effect.get("status") or "manual_review_required"),
        "evidence": effect.get("evidence") if isinstance(effect.get("evidence"), dict) else {},
        "evidence_ref": f"external-effects.jsonl#{effect_id}",
        "compensation": {"capability": capability},
        "exact_rollback": False,
        "required_operator_action": _required_operator_action(capability, str(effect.get("kind") or "unknown")),
        "blockers": blockers,
        "warnings": warnings,
    }


def build_compensation_plan(run_dir: str | Path, *, side_effect_id: str | None = None, action_id: str | None = None, dry_run: bool = True) -> dict[str, Any]:
    run_path = Path(run_dir)
    external_effects = read_external_effects(run_path)
    source = "external-effects.jsonl" if external_effects else "side-effects.jsonl"
    if external_effects:
        items = [_external_plan_item(effect) for effect in external_effects]
        if side_effect_id:
            items = [item for item in items if item.get("external_effect_id") == side_effect_id or item.get("effect_id") == side_effect_id]
        # External registry v1 does not bind effects to SafeLoop action IDs.
        if action_id:
            items = []
    else:
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
    if any(any(blocker.get("code") == "missing_external_evidence" for blocker in item.get("blockers", [])) for item in items):
        warnings.append("manual_review_required: missing external evidence blocks compensation planning")
    if any(item.get("compensation", {}).get("capability") in {"best_effort", "verified"} for item in items):
        warnings.append("operator_review_required: external compensation requires operator verification")
    status = "blocked" if blockers else "manual_review_required" if any("manual_review_required" in warning for warning in warnings) else "operator_review_required" if warnings else "ok"
    plan = {
        "schema_version": SCHEMA_VERSION,
        "run_id": _load_json(run_path / "run.json").get("run_id") if (run_path / "run.json").exists() else None,
        "mode": "dry-run" if dry_run else "plan",
        "source": source,
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


def _evidence_key(path_or_url: str) -> str:
    value = path_or_url.strip()
    if value.startswith(("http://", "https://")):
        return "url"
    return "path"


def _known_effect_ids(run_path: Path) -> set[str]:
    ids = {str(effect.get("effect_id")) for effect in read_external_effects(run_path) if effect.get("effect_id")}
    plan_path = run_path / "compensation-plan.json"
    if plan_path.exists():
        try:
            plan = _load_json(plan_path)
        except json.JSONDecodeError:
            plan = {}
        for item in plan.get("items", []) if isinstance(plan.get("items"), list) else []:
            if not isinstance(item, dict):
                continue
            for key in ("effect_id", "side_effect_id"):
                if item.get(key):
                    ids.add(str(item[key]))
    return ids


def create_compensation_result(
    run_dir: str | Path,
    *,
    effect_id: str,
    status: str,
    operator: str,
    evidence_path_or_url: str,
    quote_or_field: str,
    notes: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Write RUN_DIR/compensation-result.json for a manual operator outcome.

    This is a receipt for human/operator compensation work. It does not execute
    adapters, alter local rollback artifacts, or claim exact rollback for
    external side effects.
    """

    run_path = Path(run_dir)
    run = _load_json(run_path / "run.json")
    run_id = str(run.get("run_id") or "").strip()
    if not run_id:
        raise CompensationResultValidationError("run.json must include run_id")

    effect_value = (effect_id or "").strip()
    if not effect_value:
        raise CompensationResultValidationError("effect_id is required")
    if effect_value not in _known_effect_ids(run_path):
        raise CompensationResultValidationError(f"effect_id not found in external-effects.jsonl or compensation-plan.json: {effect_value}")

    final_status = (status or "").strip()
    if final_status not in RESULT_STATUSES:
        raise CompensationResultValidationError(f"status must be one of: {', '.join(sorted(RESULT_STATUSES))}")

    evidence_value = (evidence_path_or_url or "").strip()
    quote_value = (quote_or_field or "").strip()
    if not evidence_value:
        raise CompensationResultValidationError("evidence path or url is required")
    if not quote_value:
        raise CompensationResultValidationError("evidence quote_or_field is required")

    result: dict[str, Any] = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "run_id": run_id,
        "effect_id": effect_value,
        "status": final_status,
        "operator": (operator or "").strip() or "unknown",
        "created_at": created_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "manual_operator_result": True,
        "exact_rollback": False,
        "local_rollback_applied": False,
        "external_execution_by_safeloop": False,
        "evidence": {_evidence_key(evidence_value): evidence_value, "quote_or_field": quote_value},
        "warnings": ["manual compensation is operator-attested mitigation, not exact rollback"],
    }
    if notes:
        result["notes"] = notes
    atomic_json(run_path / "compensation-result.json", result)
    return result


def compensation_section_for_rollback(run_dir: str | Path, *, action_id: str | None = None, include_compensation: bool = False) -> dict[str, Any]:
    if include_compensation:
        plan = build_compensation_plan(run_dir, action_id=action_id, dry_run=True)
        return {"included": True, "plan_path": str(Path(run_dir) / "compensation-plan.json"), **plan}
    return {"included": False, "warnings": ["manual_review_required: side effects require --include-compensation for a compensation plan"], "exact_rollback": False}
