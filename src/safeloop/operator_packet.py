from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from safeloop.compensation import compensation_result_receipt_ref, validate_compensation_result_record
from safeloop.external_effects import ExternalEffectValidationError, read_external_effects
from safeloop.runtime_tool_exec import RuntimeToolExecError, read_runtime_tool_exec_events
from safeloop.runtime_tool_firewall import RuntimeToolFirewallError, read_runtime_tool_firewall_events
from safeloop.side_effect_ledger import read_side_effect_events

BOUNDARY_LINES = [
    "Exact rollback only applies to covered local file changes.",
    "External side effects are manual-review/compensation only.",
    "SafeLoop does not claim exact rollback for actions outside the local repo.",
    "GitHub, messaging, email, webhooks, hosted systems, and third-party services require compensation/manual review rather than exact rollback.",
    "Unknown runtime tool requests require manual review before execution.",
    "Guarded runtime tool execution only runs allowlisted read-only commands after firewall routing.",
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


def _tool_shim_coverage(tool_shims: dict, *, enabled: bool) -> str:
    if not enabled:
        return "disabled"
    coverage = str(tool_shims.get("coverage_version") or "").strip()
    if coverage:
        return coverage
    schema_version = str(tool_shims.get("schema_version") or "").strip()
    if schema_version == "tool-shims.v2":
        return "v2"
    if schema_version == "tool-shims.v1":
        return "v1"
    return "partial"


def _artifact_status(run_path: Path, name: str, data: dict) -> str:
    if not (run_path / name).exists():
        return f"{name}: not_present"
    status = _status(data.get("status"), "present")
    return f"{name}: present (status: {status})"


def _effect_id_aliases(record: dict) -> list[str]:
    aliases: list[str] = []
    for key in ("effect_id", "side_effect_id", "event_id"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            aliases.append(value.strip())
    return list(dict.fromkeys(aliases))


def _items_by_effect_id(data: dict) -> dict[str, dict]:
    items = data.get("items")
    if not isinstance(items, list):
        items = data.get("effects")
    by_id: dict[str, dict] = {}
    for alias in _effect_id_aliases(data):
        by_id[alias] = data
    if not isinstance(items, list):
        return by_id
    for item in items:
        if isinstance(item, dict):
            for alias in _effect_id_aliases(item):
                by_id[alias] = item
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


def _quarantine_items_from_plan(plan: dict) -> list[dict]:
    quarantine = plan.get("quarantine")
    if not isinstance(quarantine, dict):
        return []
    items = quarantine.get("items")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def _quarantine_evidence(item: dict) -> str:
    evidence = item.get("evidence")
    if isinstance(evidence, list) and evidence:
        return "; ".join(str(value) for value in evidence)
    return "rollback-plan.json"


def _quarantine_blockers(item: dict) -> str:
    blockers = item.get("blockers")
    if isinstance(blockers, list) and blockers:
        return "; ".join(str(value) for value in blockers)
    return "none"


def _external_outbox_items(run_path: Path) -> list[dict]:
    outbox = _load_json(run_path / "external-outbox.json")
    raw_items = outbox.get("items")
    if not isinstance(raw_items, list):
        raw_items = outbox.get("outbox")
    if not isinstance(raw_items, list):
        return []
    return [item for item in raw_items if isinstance(item, dict)]


def _runtime_tool_firewall_items(run_path: Path) -> tuple[list[dict], list[str]]:
    try:
        return read_runtime_tool_firewall_events(run_path), []
    except RuntimeToolFirewallError as exc:
        return [], [str(exc)]


def _runtime_tool_exec_items(run_path: Path) -> tuple[list[dict], list[str]]:
    try:
        return read_runtime_tool_exec_events(run_path), []
    except RuntimeToolExecError as exc:
        return [], [str(exc)]


def _outbox_item_id(item: dict, index: int) -> str:
    return str(item.get("id") or item.get("outbox_id") or f"external-outbox[{index}]")


def _outbox_item_ref(item: dict) -> str:
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    kind = str(item.get("kind") or payload.get("kind") or "external")
    target = str(item.get("target") or payload.get("target") or payload.get("url") or item.get("external_ref") or "unknown")
    return f"{kind}:{target}"


def _outbox_item_phase(item: dict) -> str:
    return str(item.get("phase") or item.get("status") or "pending")


def _outbox_item_evidence(item: dict) -> str:
    evidence = ["external-outbox.json"]
    if item.get("external_effect_id"):
        evidence.append("external-effects.jsonl")
    if item.get("compensation_result_ref"):
        evidence.append("compensation-result.json")
    return "; ".join(evidence)


def _outbox_required_action(item: dict) -> str:
    phase = _outbox_item_phase(item)
    if phase == "pending":
        return "prepare with approval/waiver binding before any external dispatch"
    if phase == "prepared":
        return "dispatch only once, then record commit evidence into external-effects.jsonl"
    if phase == "committed":
        return "review evidence and execute compensation/manual handoff if needed"
    if phase == "compensated":
        return "verify compensation receipt; do not claim exact rollback"
    return "manual review required before external dispatch or compensation"


def _side_effect_ledger_item_ref(event: dict, index: int) -> str:
    return f"side-effects:{event.get('event_id') or event.get('side_effect_id') or index}"


def _side_effect_ledger_display(event: dict, index: int) -> dict[str, str]:
    event_id = str(event.get("event_id") or event.get("side_effect_id") or f"side-effects[{index}]")
    effect_class = str(event.get("effect_class") or event.get("class") or event.get("type") or "external_side_effect")
    external_ref = str(event.get("external_ref") or event.get("target") or event_id)
    phase = str(event.get("phase") or "observed")
    capability = "manual"
    compensation = event.get("compensation")
    if isinstance(compensation, dict) and compensation.get("capability"):
        capability = str(compensation["capability"])
    return {
        "event_id": event_id,
        "effect_class": effect_class,
        "external_ref": external_ref,
        "phase": phase,
        "capability": capability,
        "schema_version": str(event.get("schema_version") or "side-effect-ledger.v1"),
    }


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
    policy_profile = str(run.get("policy_profile") or "strict-local")
    tool_shims = run.get("tool_shims") if isinstance(run.get("tool_shims"), dict) else {}
    tool_shims_enabled = bool(run.get("tool_shims_enabled") or tool_shims.get("enabled"))
    tool_shims_status = "enabled" if tool_shims_enabled else "disabled"
    tool_shim_coverage = _tool_shim_coverage(tool_shims, enabled=tool_shims_enabled)
    tool_shims_caveat = str(
        tool_shims.get("bypass_caveat")
        or "PATH shims are disabled; only explicit SafeLoop wrappers and direct firewall calls apply."
    )

    files = _file_items_from_plan(rollback_plan)
    quarantine_items = _quarantine_items_from_plan(rollback_plan)
    outbox_items = _external_outbox_items(run_path)
    firewall_items, firewall_errors = _runtime_tool_firewall_items(run_path)
    exec_items, exec_errors = _runtime_tool_exec_items(run_path)
    firewall_manual_review_items = [
        item for item in firewall_items if str(item.get("route") or "") == "manual_review"
    ]
    exec_blocked_items = [
        item for item in exec_items if str(item.get("status") or "") in {"blocked", "execution_error", "timed_out"}
    ]
    unsafe_outbox_boundary = _unsafe_outbox_boundary(run, outbox_items)
    external_items = list(external_evidence or [])
    external_registry_errors: list[str] = []
    try:
        external_effect_records = read_external_effects(run_path)
    except ExternalEffectValidationError as exc:
        external_effect_records = []
        external_registry_errors.append(str(exc))
    external_registry_exists = (run_path / "external-effects.jsonl").exists()
    external_effect_by_item: dict[str, dict] = {}
    external_outbox_by_item: dict[str, dict] = {}
    side_effect_ledger_by_item: dict[str, dict] = {}
    side_effect_ledger_errors: list[str] = []
    for effect in external_effect_records:
        item_ref = f"{effect.get('kind', 'unknown')}:{effect.get('target', effect.get('effect_id', 'unknown'))}"
        external_items.append(item_ref)
        external_effect_by_item[item_ref] = effect
    if not external_registry_exists and not external_registry_errors:
        try:
            side_effect_events = read_side_effect_events(run_path / "side-effects.jsonl")
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            side_effect_events = []
            if (run_path / "side-effects.jsonl").exists():
                side_effect_ledger_errors.append(str(exc))
        for index, event in enumerate(side_effect_events):
            if not isinstance(event, dict):
                continue
            schema_version = event.get("schema_version")
            effect_class = event.get("effect_class") or event.get("class") or event.get("type")
            if schema_version != "side-effect-ledger.v1" or effect_class in {None, "file", "git"}:
                continue
            item_ref = _side_effect_ledger_item_ref(event, index)
            external_items.append(item_ref)
            side_effect_ledger_by_item[item_ref] = event
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
        item_id = _outbox_item_id(item, index)
        external_items.append(item_id)
        external_outbox_by_item[item_id] = item

    completed_result_statuses = {"compensation_completed", "completed", "verified"}
    completed_effect_ids = {
        effect_id
        for effect_id, result in result_items_by_effect_id.items()
        if result.get("status") in completed_result_statuses
        and compensation_result_receipt_ref(result)
        and not compensation_result_errors_by_effect_id.get(effect_id)
    }

    def _effect_id_for_external_item(item: str) -> str:
        effect = external_effect_by_item.get(item)
        if effect:
            return str(effect.get("effect_id") or "")
        ledger_event = side_effect_ledger_by_item.get(item)
        if ledger_event:
            aliases = _effect_id_aliases(ledger_event)
            for alias in aliases:
                if alias in result_items_by_effect_id:
                    return alias
            for alias in aliases:
                if alias in plan_items_by_effect_id:
                    return alias
            if aliases:
                return aliases[0]
        return ""

    outbox_item_ids = {_outbox_item_id(outbox, index) for index, outbox in enumerate(outbox_items)}
    receipt_correlatable_items = [item for item in external_items if item not in outbox_item_ids]
    external_registry_compensation_complete = bool(receipt_correlatable_items) and not outbox_items and all(
        (effect_id := _effect_id_for_external_item(item)) and effect_id in completed_effect_ids
        for item in receipt_correlatable_items
    )

    next_action = "verify_only"
    outbox_review_required = any(_outbox_item_phase(item) in {"pending", "prepared", "manual_review"} for item in outbox_items)
    if unsafe_outbox_boundary:
        next_action = "pending_unbound_external_outbox"
    elif external_registry_compensation_complete:
        next_action = "compensation_complete_verify_receipt"
    elif outbox_review_required:
        next_action = "external_outbox_review_required"
    elif external_items:
        next_action = "compensation_review_required"
    elif exec_blocked_items:
        next_action = "runtime_tool_exec_manual_review_required"
    elif firewall_manual_review_items:
        next_action = "runtime_tool_firewall_manual_review_required"
    elif files:
        next_action = "rollback_available"
    if issues or external_registry_errors or compensation_result_errors or side_effect_ledger_errors or firewall_errors or exec_errors:
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
        f"- firewall policy profile: {_cell(policy_profile)}",
        f"- tool-shims: {tool_shims_status}",
        f"- tool-shim coverage: {_cell(tool_shim_coverage)}",
        f"- tool-shims bypass caveat: {_cell(tool_shims_caveat)}",
        "",
        "## 2. Artifact verification",
        f"- verify-artifacts status: {verification_status}",
        f"- local anchor status: {local_anchor_status}",
        f"- evidence packet status: {evidence_packet_status}",
        "- issues / warnings:",
    ]
    if issues or warnings or external_registry_errors or compensation_result_errors or side_effect_ledger_errors or firewall_errors or exec_errors:
        for issue in issues:
            lines.append(f"  - issue: {_cell(issue)}")
        for warning in warnings:
            lines.append(f"  - warning: {_cell(warning)}")
        for error in external_registry_errors:
            lines.append(f"  - issue: invalid_external_effect_registry: {_cell(error)}")
        for error in side_effect_ledger_errors:
            lines.append(f"  - issue: invalid_side_effect_ledger: {_cell(error)}")
        for error in compensation_result_errors:
            lines.append(f"  - issue: invalid_compensation_result: {_cell(error)}")
        for error in firewall_errors:
            lines.append(f"  - issue: invalid_runtime_tool_firewall: {_cell(error)}")
        for error in exec_errors:
            lines.append(f"  - issue: invalid_runtime_tool_exec: {_cell(error)}")
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
    for item in quarantine_items:
        lines.append(
            _row(
                [
                    item.get("item_id"),
                    "quarantine",
                    item.get("original_path"),
                    item.get("rollback_status", "manual_review_required"),
                    str(item.get("exact_rollback", False)).lower(),
                    _quarantine_evidence(item),
                ]
            )
        )
    for item in firewall_items:
        evidence = ["runtime-tool-firewall.jsonl"]
        if item.get("action_id"):
            evidence.append("action-events.jsonl")
        if item.get("quarantine_item_id"):
            evidence.append("quarantine")
        if item.get("outbox_id"):
            evidence.append("external-outbox.json")
        lines.append(
            _row(
                [
                    item.get("event_id"),
                    "runtime_tool_firewall",
                    item.get("target"),
                    item.get("route"),
                    str(item.get("exact_rollback", False)).lower(),
                    "; ".join(evidence),
                ]
            )
        )
    for item in exec_items:
        evidence = ["runtime-tool-exec.jsonl", "runtime-tool-firewall.jsonl"]
        if item.get("stdout_path"):
            evidence.append(str(item["stdout_path"]))
        if item.get("stderr_path"):
            evidence.append(str(item["stderr_path"]))
        lines.append(
            _row(
                [
                    item.get("exec_id"),
                    "runtime_tool_exec",
                    item.get("target"),
                    item.get("status"),
                    "false",
                    "; ".join(evidence),
                ]
            )
        )
    for item in external_items:
        if item in external_outbox_by_item:
            outbox_item = external_outbox_by_item[item]
            lines.append(
                _row(
                    [
                        _outbox_item_id(outbox_item, 0),
                        "external_outbox",
                        _outbox_item_ref(outbox_item),
                        _outbox_item_phase(outbox_item),
                        "false",
                        _outbox_item_evidence(outbox_item),
                    ]
                )
            )
            continue
        evidence_ref = "external-effects.jsonl" if item in external_effect_by_item else ("side-effects.jsonl" if item in side_effect_ledger_by_item else item)
        effect = external_effect_by_item.get(item)
        ledger_display = _side_effect_ledger_display(side_effect_ledger_by_item[item], 0) if item in side_effect_ledger_by_item else None
        effect_id = ""
        if effect:
            effect_id = str(effect.get("effect_id") or "")
        elif ledger_display:
            effect_id = _effect_id_for_external_item(item)
        display_item = str(effect.get("effect_id") or item) if effect else (effect_id or ledger_display["event_id"] if ledger_display else item)
        display_type = str(effect.get("kind") or "external_side_effect") if effect else (ledger_display["effect_class"] if ledger_display else "external_side_effect")
        display_ref = str(effect.get("target") or item) if effect else (ledger_display["external_ref"] if ledger_display else item)
        display_status = str(effect.get("status") or "manual_review_required") if effect else "manual_review_required"
        result_item = result_items_by_effect_id.get(effect_id, {}) if effect_id else {}
        receipt = compensation_result_receipt_ref(result_item) if result_item else None
        if effect_id and compensation_result_errors_by_effect_id.get(effect_id):
            display_status = "manual_review_required: missing compensation receipt"
        elif result_item and result_item.get("status"):
            display_status = str(result_item["status"])
            evidence_ref = (
                "external-effects.jsonl; compensation-result.json"
                if effect
                else "side-effects.jsonl; compensation-result.json"
            )
            if receipt:
                evidence_ref += f"; receipt: {receipt}"
        lines.append(_row([display_item, display_type, display_ref, display_status, "false", evidence_ref]))
        if effect:
            lines.append(_row([item, "external_side_effect", item, display_status, "false", evidence_ref]))
        if display_status in completed_result_statuses:
            manual_review_status = display_status
            compensation_status = display_status
        else:
            manual_review_status = "queued"
            compensation_status = "compensation_review_required"
        lines.append(_row([item, "manual_review_item", item, manual_review_status, "false", evidence_ref]))
        lines.append(_row([item, "compensation_item", item, compensation_status, "false", evidence_ref]))

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
    ])
    for item in quarantine_items:
        command = item.get("restore_command") or "manual review"
        lines.append(
            _row(
                [
                    f"quarantine restore: {item.get('original_path')}",
                    item.get("item_id"),
                    item.get("rollback_status", "manual_review_required"),
                    str(item.get("exact_rollback", False)).lower(),
                    _quarantine_blockers(item),
                    command,
                ]
            )
        )
    lines.extend([
        "",
        "## 5. External compensation / manual review status",
        f"- runtime-tool-firewall.jsonl: {'invalid' if firewall_errors else ('present' if firewall_items else 'not_present')}",
        f"- runtime-tool-exec.jsonl: {'invalid' if exec_errors else ('present' if exec_items else 'not_present')}",
        f"- external-outbox.json: {'present' if outbox_items else 'not_present'}",
        f"- external-effects.jsonl: {'invalid' if external_registry_errors else ('present' if external_registry_exists else 'not_present')}",
        f"- side-effects.jsonl: {'invalid' if side_effect_ledger_errors else ('present' if side_effect_ledger_by_item else 'not_present')}",
        f"- legacy side-effect ledger compatibility: {'manual_review_required for side-effect-ledger.v1 external entries' if side_effect_ledger_by_item else 'not_applicable'}",
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
            if item in external_outbox_by_item:
                outbox_item = external_outbox_by_item[item]
                lines.append(
                    _row(
                        [
                            item,
                            "external_outbox",
                            outbox_item.get("compensation_capability", "manual"),
                            "false",
                            _outbox_required_action(outbox_item),
                            _outbox_item_evidence(outbox_item),
                        ]
                    )
                )
                continue
            effect = external_effect_by_item.get(item, {})
            ledger_display = _side_effect_ledger_display(side_effect_ledger_by_item[item], 0) if item in side_effect_ledger_by_item else None
            effect_id = str(effect.get("effect_id") or "") if effect else (_effect_id_for_external_item(item) if ledger_display else "")
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
            if item in side_effect_ledger_by_item:
                capability = _side_effect_ledger_display(side_effect_ledger_by_item[item], 0)["capability"]
            evidence_ref = "external-effects.jsonl" if item in external_effect_by_item else ("side-effects.jsonl" if item in side_effect_ledger_by_item else item)
            evidence_parts = [evidence_ref]
            if effect_id and (run_path / "compensation-plan.json").exists() and plan_item:
                evidence_parts.append("compensation-plan.json")
            if effect_id and (run_path / "compensation-result.json").exists() and result_item:
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
    if external_items or firewall_manual_review_items or exec_blocked_items:
        for item in external_items:
            action = "review evidence and execute compensation/manual handoff if needed"
            if item in external_outbox_by_item:
                action = _outbox_required_action(external_outbox_by_item[item])
            if unsafe_outbox_boundary:
                action = "Do not approve, resume, or dispatch external side effects from this outbox item; pending shadow review only"
            lines.append(_row([item, "action outside the local repo", "local rollback cannot prove the external action was undone", action]))
        for item in firewall_manual_review_items:
            event_id = str(item.get("event_id") or "runtime-tool-firewall")
            target = str(item.get("target") or "unknown")
            route_reason = str(item.get("route_reason") or "unrecognized tool semantics")
            lines.append(
                _row(
                    [
                        event_id,
                        route_reason,
                        f"tool request held before execution: {target}",
                        "review the tool intent, then re-run with explicit target kind or use quarantine/outbox",
                    ]
                )
            )
        for item in exec_blocked_items:
            exec_id = str(item.get("exec_id") or "runtime-tool-exec")
            target = str(item.get("target") or "unknown")
            block_reason = str(item.get("block_reason") or item.get("status") or "execution did not complete")
            lines.append(
                _row(
                    [
                        exec_id,
                        block_reason,
                        f"tool execution held or failed: {target}",
                        "review the command and firewall route before retrying execution",
                    ]
                )
            )
    elif external_registry_errors:
        for error in external_registry_errors:
            lines.append(_row(["external-effects.jsonl", "invalid_external_effect_registry", "corrupt registry evidence can mask external rollback overclaims", f"block packet use until registry is corrected: {error}"]))
    elif side_effect_ledger_errors:
        for error in side_effect_ledger_errors:
            lines.append(_row(["side-effects.jsonl", "invalid_side_effect_ledger", "corrupt legacy ledger evidence can mask external side effects", f"block packet use until legacy side-effect ledger is corrected: {error}"]))
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
