from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

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


def _file_items_from_plan(plan: dict) -> list[str]:
    files = plan.get("files") if isinstance(plan.get("files"), dict) else {}
    items: list[str] = []
    for key in ["modified", "created", "deleted"]:
        values = files.get(key) or []
        if isinstance(values, list):
            items.extend(str(v) for v in values)
    return sorted(dict.fromkeys(items))


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
    external_items = list(external_evidence or [])

    next_action = "verify_only"
    if external_items:
        next_action = "compensation_review_required"
    elif files:
        next_action = "rollback_available"
    if issues:
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
    if issues or warnings:
        for issue in issues:
            lines.append(f"  - issue: {_cell(issue)}")
        for warning in warnings:
            lines.append(f"  - warning: {_cell(warning)}")
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
        lines.append(_row([item, "external_side_effect", item, "manual_review_required", "false", item]))
        lines.append(_row([item, "manual_review_item", item, "queued", "false", item]))
        lines.append(_row([item, "compensation_item", item, "compensation_review_required", "false", item]))

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
        "## 5. Compensation decision table",
        "Compensation capability enum: none, manual, best_effort, verified",
        _row(["Side effect", "Adapter", "Compensation capability", "Exact rollback", "Required action", "Evidence"]),
        _row(["---", "---", "---", "---", "---", "---"]),
    ])
    if external_items:
        for item in external_items:
            lines.append(_row([item, compensation_adapter, "manual", "false", "Review and compensate manually; do not treat local rollback as external rollback.", item]))
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
            lines.append(_row([item, "action outside the local repo", "local rollback cannot prove the external action was undone", "review evidence and execute compensation/manual handoff if needed"]))
    else:
        lines.append(_row(["none", "no external side effects recorded", "low", "verify packet and proceed with local rollback if needed"]))

    lines.extend([
        "",
        "## 7. Recommended next action",
        next_action,
        "",
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
