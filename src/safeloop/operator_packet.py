from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from safeloop.compensation import compensation_result_receipt_ref, validate_compensation_result_record
from safeloop.external_effects import ExternalEffectValidationError, read_external_effects

BOUNDARY_LINES = [
    "Exact rollback only applies to covered local file changes.",
    "External side effects are manual-review/compensation only.",
    "SafeLoop does not claim exact rollback for actions outside the local repo.",
    "GitHub, messaging, email, webhooks, hosted systems, and third-party services require compensation/manual review rather than exact rollback.",
]


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _cell(value: object) -> str:
    text = "" if value is None else str(value)
    return text.replace("\n", " ").replace("|", "\\|")


def _row(cells: Iterable[object]) -> str:
    return "| " + " | ".join(_cell(cell) for cell in cells) + " |"


def _status(value: object, default: str = "unknown") -> str:
    return str(value or default)


def _artifact_status(run_path: Path, name: str, data: dict) -> str:
    if not (run_path / name).exists():
        return f"{name}: not_present"
    status = _status(data.get("status"), "present")
    return f"{name}: present (status: {status})"


def _items_by_effect_id(data: dict) -> dict[str, dict]:
    items = data.get("items")
    if not isinstance(items, list):
        items = data.get("effects")
    by_id: dict[str, dict] = {}
    if isinstance(data.get("effect_id"), str) and data.get("effect_id"):
        by_id[str(data["effect_id"])] = data
    if not isinstance(items, list):
        return by_id
    for item in items:
        if isinstance(item, dict) and item.get("effect_id"):
            by_id[str(item["effect_id"])] = item
    return by_id


def _first_present(mapping: dict, names: Iterable[str]) -> str | None:
    for name in names:
        value = mapping.get(name)
        if value:
            return str(value)
    return None


def _file_items_from_plan(plan: dict) -> list[str]:
    files = plan.get("files")
    if not isinstance(files, dict):
        files = plan.get("covered_local_file_changes")
    if not isinstance(files, dict):
        files = {}

    items: list[str] = []
    for key in ["modified", "created", "deleted"]:
        values = files.get(key) or []
        if isinstance(values, list):
            items.extend(str(v) for v in values)
    return sorted(dict.fromkeys(items))


def _external_outbox_items(run_path: Path) -> list[dict]:
    outbox = _load_json(run_path / "external-outbox.json")
    raw_items = outbox.get("items")
    if not isinstance(raw_items, list):
        raw_items = outbox.get("outbox")
    if not isinstance(raw_items, list):
        return []
    return [item for item in raw_items if isinstance(item, dict)]


def _outbox_item_id(item: dict, index: int) -> str:
    return str(item.get("id") or item.get("outbox_id") or f"external-outbox[{index}]")


def _outbox_item_lifecycle_bound(item: dict) -> bool:
    has_digest = bool(item.get("approval_request_digest"))
    has_status = bool(item.get("approval_status"))
    has_decision = bool(item.get("decision_id") or item.get("waiver_id"))
    return bool(has_digest and has_status and has_decision and item.get("dispatch_allowed") is True)


def _unsafe_outbox_boundary(run: dict, outbox_items: list[dict]) -> bool:
    return bool(run.get("approval_policy") == "unsafe_allow_without_hooks" and outbox_items)


def _suggested_command(run_dir: Path, run_id: str, checkpoint_id: str, file_path: str | None = None) -> str:
    cmd = f'python -m safeloop.cli rollback apply "{run_dir}" "{run_id}" "{checkpoint_id}"'
    if file_path:
        cmd += f" --files {file_path}"
    return cmd


def render_operator_packet_v2(
    run_dir: str | Path,
    *,
    output_path: str | Path | None = None,
    external_evidence: Iterable[str] | None = None,
    compensation_adapter: str = "manual",
) -> str:
    """Render a deterministic local SafeLoop operator packet v2 markdown artifact.

    The packet is intentionally file-backed and advisory. It does not add new
    rollback behavior; it summarizes existing local artifacts and keeps external
    effects in compensation/manual-review territory.
    """

    run_path = Path(run_dir)
    run = _load_json(run_path / "run.json")
    verification = _load_json(run_path / "verification" / "verify-artifacts-result.json")
    rollback_plan = _load_json(run_path / "rollback-plan.json")
    compensation_plan = _load_json(run_path / "compensation-plan.json")
    compensation_result = _load_json(run_path / "compensation-result.json")
    plan_items_by_effect_id = _items_by_effect_id(compensation_plan)
    result_items_by_effect_id = _items_by_effect_id(compensation_result)

    run_id = str(run.get("run_id") or rollback_plan.get("run_id") or "unknown")
    task_id = str(run.get("task_id") or "unknown")
    run_status = _status(run.get("status"))
    started_at = str(run.get("started_at") or run.get("created_at") or "unknown")
    ended_at = str(run.get("ended_at") or "unknown")
    checkpoint_id = str(rollback_plan.get("checkpoint_id") or "cp-0001")
    latest_hash = str(
        verification.get("latest_event_hash")
        or run.get("latest_event_hash")
        or run.get("latest_hash")
        or "unknown"
    )
    verification_status = _status(verification.get("status"), "missing")
    issues = verification.get("issues") or []
    warnings = verification.get("warnings") or []
    local_anchor_status = "present" if (run_path / "local-anchor.json").exists() else "not_present"
    evidence_packet_status = "present" if any((run_path / name).exists() for name in ["operator-packet.md", "retrieved_context.json"]) else "not_present"

    files = _file_items_from_plan(rollback_plan)
    outbox_items = _external_outbox_items(run_path)
    unsafe_outbox_boundary = _unsafe_outbox_boundary(run, outbox_items)
    external_items = list(external_evidence or [])
    external_registry_errors: list[str] = []
    try:
        external_effect_records = read_external_effects(run_path)
    except ExternalEffectValidationError as exc:
        external_effect_records = []
        external_registry_errors.append(str(exc))
    external_effect_by_item: dict[str, dict] = {}
    for effect in external_effect_records:
        item_ref = f"{effect.get('kind', 'unknown')}:{effect.get('target', effect.get('effect_id', 'unknown'))}"
        external_items.append(item_ref)
        external_effect_by_item[item_ref] = effect
    known_effect_ids = {str(effect.get("effect_id")) for effect in external_effect_records if effect.get("effect_id")}
    known_effect_ids.update(str(effect_id) for effect_id in plan_items_by_effect_id if effect_id)
    compensation_result_errors: list[str] = []
    compensation_result_errors_by_effect_id: dict[str, list[str]] = {}
    if compensation_result:
        records: list[tuple[str, dict]] = []
        if isinstance(compensation_result.get("effect_id"), str) and compensation_result.get("effect_id"):
            records.append(("compensation-result.json", compensation_result))
        raw_items = compensation_result.get("items")
        if not isinstance(raw_items, list):
            raw_items = compensation_result.get("effects")
        if isinstance(raw_items, list):
            for index, item in enumerate(raw_items):
                if isinstance(item, dict):
                    records.append((f"compensation-result.json#items[{index}]", item))
        for location, record in records:
            record_errors = validate_compensation_result_record(record, known_effect_ids=known_effect_ids or None, location=location)
            compensation_result_errors.extend(record_errors)
            effect_id = str(record.get("effect_id") or "").strip()
            if effect_id and record_errors:
                compensation_result_errors_by_effect_id.setdefault(effect_id, []).extend(record_errors)
    for index, item in enumerate(outbox_items):
        external_items.append(_outbox_item_id(item, index))

    next_action = "verify_only"
    if unsafe_outbox_boundary:
        next_action = "pending_unbound_external_outbox"
    elif external_items:
        next_action = "compensation_review_required"
    elif files:
        next_action = "rollback_available"
    if issues or external_registry_errors or compensation_result_errors:
        next_action = "blocked"

    lines: list[str] = [
        "# SafeLoop Operator Packet v2",
        "",
        "## 1. Run summary",
        f"- run_id: {run_id}",
        f"- task_id: {task_id}",
        f"- status: {run_status}",
        f"- started_at: {started_at}",
        f"- ended_at: {ended_at}",
        f"- latest event hash: {latest_hash}",
        f"- verification status: {verification_status}",
        "",
        "## 2. Artifact verification",
        f"- verify-artifacts status: {verification_status}",
        f"- local anchor status: {local_anchor_status}",
        f"- evidence packet status: {evidence_packet_status}",
        "- issues / warnings:",
    ]
    if issues or warnings or external_registry_errors or compensation_result_errors:
        for issue in issues:
            lines.append(f"  - issue: {_cell(issue)}")
        for warning in warnings:
            lines.append(f"  - warning: {_cell(warning)}")
        for error in external_registry_errors:
            lines.append(f"  - issue: invalid_external_effect_registry: {_cell(error)}")
        for error in compensation_result_errors:
            lines.append(f"  - issue: invalid_compensation_result: {_cell(error)}")
    else:
        lines.append("  - none")

    lines.extend([
        "",
        "## 3. Change summary",
        _row(["Item", "Type", "Path / Ref", "Status", "Exact rollback", "Evidence"]),
        _row(["---", "---", "---", "---", "---", "---"]),
    ])
    for file_path in files or ["covered local files"]:
        lines.append(_row([file_path, "local_file", file_path, "rollback_available", "true", "rollback-plan.json"]))
    lines.append(_row([checkpoint_id, "action_group", checkpoint_id, "rollback_available", "true", "rollback-plan.json"]))
    for item in external_items:
        evidence_ref = "external-effects.jsonl" if item in external_effect_by_item else item
        effect = external_effect_by_item.get(item)
        display_item = str(effect.get("effect_id") or item) if effect else item
        display_type = str(effect.get("kind") or "external_side_effect") if effect else "external_side_effect"
        display_ref = str(effect.get("target") or item) if effect else item
        display_status = str(effect.get("status") or "manual_review_required") if effect else "manual_review_required"
        if effect and compensation_result_errors_by_effect_id.get(str(effect.get("effect_id") or "")):
            display_status = "manual_review_required: missing compensation receipt"
        lines.append(_row([display_item, display_type, display_ref, display_status, "false", evidence_ref]))
        if effect:
            lines.append(_row([item, "external_side_effect", item, display_status, "false", evidence_ref]))
        lines.append(_row([item, "manual_review_item", item, "queued", "false", evidence_ref]))
        lines.append(_row([item, "compensation_item", item, "compensation_review_required", "false", evidence_ref]))

    first_file = files[0] if files else "service.md"
    no_blockers = "none" if verification_status in {"valid", "ok"} else "verify-artifacts not valid"
    lines.extend([
        "",
        "## 4. Rollback decision table",
        _row(["Selection", "Scope", "Rollback status", "Exact rollback", "Blockers", "Suggested command"]),
        _row(["---", "---", "---", "---", "---", "---"]),
        _row(["all covered local files", "covered local file changes", "available" if files else "review_required", "true", no_blockers, _suggested_command(run_path, run_id, checkpoint_id)]),
        _row(["selected file rollback", first_file, "available", "true", no_blockers, _suggested_command(run_path, run_id, checkpoint_id, first_file)]),
        _row(["selected hunk rollback", "review hunk manifest before apply", "review_required", "true", "operator must select hunk", "review hunk-manifest.json, then run rollback apply with selected hunks"]),
        _row(["selected action group rollback", checkpoint_id, "available", "true", no_blockers, _suggested_command(run_path, run_id, checkpoint_id)]),
        "",
        "## 5. External compensation / manual review status",
        f"- external-effects.jsonl: {'invalid' if external_registry_errors else ('present' if external_effect_records else 'not_present')}",
        f"- {_artifact_status(run_path, 'compensation-plan.json', compensation_plan)}",
        f"- compensation-result.json: invalid (manual_review_required: missing compensation receipt)" if compensation_result_errors else f"- {_artifact_status(run_path, 'compensation-result.json', compensation_result)}",
        "- This table is separate from local rollback. It records compensation/manual review only and never exact external rollback.",
        "",
        "## 5. Compensation decision table",
        "Compensation capability enum: none, manual, best_effort, verified",
        _row(["Side effect", "Adapter", "Compensation capability", "Exact rollback", "Required action", "Evidence"]),
        _row(["---", "---", "---", "---", "---", "---"]),
    ])
    if external_items:
        for item in external_items:
            effect = external_effect_by_item.get(item, {})
            effect_id = str(effect.get("effect_id") or "")
            plan_item = plan_items_by_effect_id.get(effect_id, {}) if effect_id else {}
            result_item = result_items_by_effect_id.get(effect_id, {}) if effect_id else {}
            planned_action = _first_present(plan_item, ["planned_action", "action", "description"])
            result_status = _first_present(result_item, ["status", "result_status"])
            receipt = compensation_result_receipt_ref(result_item) if result_item else None
            result_errors = validate_compensation_result_record(
                result_item,
                known_effect_ids=known_effect_ids or None,
                location=f"compensation-result.json#{effect_id}",
            ) if result_item else []
            required_action = "Review and compensate manually; do not treat local rollback as external rollback."
            if result_errors:
                result_status = None
                required_action = "; ".join(result_errors)
            elif planned_action or result_status:
                parts = []
                if planned_action:
                    parts.append(f"planned: {planned_action}")
                if result_status:
                    parts.append(f"result: {result_status}")
                required_action = "; ".join(parts)
            if unsafe_outbox_boundary:
                required_action = "Do not dispatch externally; pending shadow review only until approval/waiver lifecycle binding exists."
            capability = effect.get("compensation_capability", "manual")
            evidence_ref = "external-effects.jsonl" if item in external_effect_by_item else item
            evidence_parts = [evidence_ref]
            if effect and (run_path / "compensation-plan.json").exists():
                evidence_parts.append("compensation-plan.json")
            if effect and (run_path / "compensation-result.json").exists():
                evidence_parts.append("compensation-result.json")
            if receipt:
                evidence_parts.append(f"receipt: {receipt}")
            lines.append(_row([item, compensation_adapter, capability, "false", required_action, "; ".join(evidence_parts)]))
    else:
        lines.append(_row(["none recorded", "none", "none", "false", "No external side effect compensation item recorded.", "review-summary.json"]))

    lines.extend([
        "",
        "## 6. Manual review queue",
        _row(["Item", "Reason", "Risk", "Recommended operator action"]),
        _row(["---", "---", "---", "---"]),
    ])
    if external_items:
        for item in external_items:
            action = "review evidence and execute compensation/manual handoff if needed"
            if unsafe_outbox_boundary:
                action = "Do not approve, resume, or dispatch external side effects from this outbox item; pending shadow review only"
            lines.append(_row([item, "action outside the local repo", "local rollback cannot prove the external action was undone", action]))
    elif external_registry_errors:
        for error in external_registry_errors:
            lines.append(_row(["external-effects.jsonl", "invalid_external_effect_registry", "corrupt registry evidence can mask external rollback overclaims", f"block packet use until registry is corrected: {error}"]))
    else:
        lines.append(_row(["none", "no external side effects recorded", "low", "verify packet and proceed with local rollback if needed"]))

    lines.extend([
        "",
        "## 7. Recommended next action",
        f"recommended next action: {next_action}",
        "",
    ])
    if unsafe_outbox_boundary:
        lines.extend([
            "### Demo-only unsafe exception / pending shadow review only",
            "This packet records a local/demo-only unsafe exception. It is not evidence that external dispatch is safe or approved.",
            "Do not approve, resume, or dispatch external side effects from this outbox item unless it is explicitly bound to an approval decision or waiver and compensation/handoff status is recorded.",
            "Required external-outbox lifecycle fields before dispatch:",
            "- approval_request_digest",
            "- approval_status",
            "- decision_id or waiver_id",
            "- dispatch_allowed: false unless lifecycle-bound",
            "",
            _row(["Outbox item", "Status", "Lifecycle bound", "Missing / required binding", "Operator action"]),
            _row(["---", "---", "---", "---", "---"]),
        ])
        for index, item in enumerate(outbox_items):
            item_id = _outbox_item_id(item, index)
            lifecycle_bound = _outbox_item_lifecycle_bound(item)
            missing = []
            if not item.get("approval_request_digest"):
                missing.append("approval_request_digest")
            if not item.get("approval_status"):
                missing.append("approval_status")
            if not (item.get("decision_id") or item.get("waiver_id")):
                missing.append("decision_id or waiver_id")
            if item.get("dispatch_allowed") is not True:
                missing.append("dispatch_allowed=true")
            if lifecycle_bound:
                missing_text = "none"
                action = "verify approval/waiver and compensation status before dispatch"
            else:
                missing_text = ", ".join(missing)
                action = "do not dispatch; pending shadow review only"
            lines.append(_row([item_id, item.get("status", "unknown"), f"lifecycle_bound={str(lifecycle_bound).lower()}", missing_text, action]))
        lines.append("")
    lines.extend([
        "## 8. Boundary statement",
        *[f"- {line}" for line in BOUNDARY_LINES],
        "",
    ])
    return "\n".join(lines)


def write_operator_packet_v2(
    run_dir: str | Path,
    *,
    output_path: str | Path | None = None,
    external_evidence: Iterable[str] | None = None,
    compensation_adapter: str = "manual",
) -> Path:
    run_path = Path(run_dir)
    out = Path(output_path) if output_path is not None else run_path / "operator-packet-v2.md"
    out.write_text(
        render_operator_packet_v2(
            run_path,
            output_path=out,
            external_evidence=external_evidence,
            compensation_adapter=compensation_adapter,
        ),
        encoding="utf-8",
    )
    return out
