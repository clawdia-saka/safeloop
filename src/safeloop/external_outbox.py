from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from safeloop.agent_watchdog import atomic_json
from safeloop.external_effects import (
    ALLOWED_COMPENSATION_CAPABILITIES,
    ALLOWED_KINDS,
    record_external_effect,
)

OUTBOX_SCHEMA_VERSION = "external-outbox.v1"
OUTBOX_ITEM_SCHEMA_VERSION = "external-outbox-item.v1"
ALLOWED_PHASES = {"pending", "prepared", "committed", "compensated", "manual_review"}
PREPARED_APPROVAL_STATUSES = {"approved", "waived"}
_SENSITIVE_RE = re.compile(r"(?i)(authorization|bearer\s+[a-z0-9._~+/=-]+|api[_-]?key|secret|token|password)[:= ]")


class ExternalOutboxError(ValueError):
    """Raised when an external outbox lifecycle transition is unsafe."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _outbox_path(run_dir: str | Path) -> Path:
    return Path(run_dir) / "external-outbox.json"


def _load_run_id(run_path: Path) -> str:
    try:
        run = json.loads((run_path / "run.json").read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ExternalOutboxError(f"missing run.json in run directory: {run_path}") from exc
    except json.JSONDecodeError as exc:
        raise ExternalOutboxError(f"invalid run.json in run directory: {run_path}") from exc
    run_id = str(run.get("run_id") or "").strip()
    if not run_id:
        raise ExternalOutboxError("run.json must include run_id")
    return run_id


def _validate_no_sensitive_payload(*values: str) -> None:
    for value in values:
        if _SENSITIVE_RE.search(value or ""):
            raise ExternalOutboxError("raw sensitive payload storage is not allowed; store minimal evidence references only")


def _evidence_key(path_or_url: str) -> str:
    value = path_or_url.strip()
    if value.startswith(("http://", "https://")):
        return "url"
    return "path"


def _evidence(path_or_url: str, quote_or_field: str) -> dict[str, str]:
    evidence_value = (path_or_url or "").strip()
    quote_value = (quote_or_field or "").strip()
    if not evidence_value:
        raise ExternalOutboxError("evidence path or url is required")
    if not quote_value:
        raise ExternalOutboxError("evidence quote_or_field is required")
    _validate_no_sensitive_payload(evidence_value, quote_value)
    return {_evidence_key(evidence_value): evidence_value, "quote_or_field": quote_value}


def _empty_outbox(run_id: str) -> dict[str, Any]:
    return {
        "schema_version": OUTBOX_SCHEMA_VERSION,
        "run_id": run_id,
        "items": [],
        "counts": {phase: 0 for phase in sorted(ALLOWED_PHASES)},
    }


def _counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts = {phase: 0 for phase in sorted(ALLOWED_PHASES)}
    for item in items:
        phase = str(item.get("phase") or "manual_review")
        counts[phase if phase in counts else "manual_review"] += 1
    return counts


def _load_outbox(run_path: Path) -> dict[str, Any]:
    run_id = _load_run_id(run_path)
    path = _outbox_path(run_path)
    if not path.exists():
        return _empty_outbox(run_id)
    try:
        outbox = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ExternalOutboxError(f"invalid external-outbox.json: {path}") from exc
    if not isinstance(outbox, dict):
        raise ExternalOutboxError("external-outbox.json must be an object")
    if outbox.get("schema_version") != OUTBOX_SCHEMA_VERSION:
        raise ExternalOutboxError(f"external-outbox.json schema_version must be {OUTBOX_SCHEMA_VERSION}")
    if str(outbox.get("run_id") or "") != run_id:
        raise ExternalOutboxError("external-outbox.json run_id does not match run.json")
    items = outbox.get("items")
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise ExternalOutboxError("external-outbox.json items must be a list of objects")
    outbox["counts"] = _counts(items)
    for item in items:
        _validate_item(item)
    return outbox


def _write_outbox(run_path: Path, outbox: dict[str, Any]) -> None:
    items = outbox.get("items")
    if not isinstance(items, list):
        raise ExternalOutboxError("external-outbox.json items must be a list")
    outbox["counts"] = _counts([item for item in items if isinstance(item, dict)])
    outbox["updated_at"] = _utc_now()
    atomic_json(_outbox_path(run_path), outbox)


def _validate_item(item: dict[str, Any]) -> None:
    if item.get("schema_version") != OUTBOX_ITEM_SCHEMA_VERSION:
        raise ExternalOutboxError(f"external outbox item schema_version must be {OUTBOX_ITEM_SCHEMA_VERSION}")
    for field in ["outbox_id", "run_id", "kind", "target", "action", "phase", "status", "created_at"]:
        if not str(item.get(field) or "").strip():
            raise ExternalOutboxError(f"{field} is required")
    if item.get("kind") not in ALLOWED_KINDS:
        raise ExternalOutboxError(f"kind must be one of: {', '.join(sorted(ALLOWED_KINDS))}")
    if item.get("phase") not in ALLOWED_PHASES:
        raise ExternalOutboxError(f"phase must be one of: {', '.join(sorted(ALLOWED_PHASES))}")
    if item.get("exact_rollback") is not False:
        raise ExternalOutboxError("external outbox exact_rollback must always be false")
    if item.get("compensation_capability") not in ALLOWED_COMPENSATION_CAPABILITIES:
        raise ExternalOutboxError(
            f"compensation_capability must be one of: {', '.join(sorted(ALLOWED_COMPENSATION_CAPABILITIES))}"
        )
    evidence = item.get("evidence")
    if not isinstance(evidence, dict) or not str(evidence.get("path") or evidence.get("url") or "").strip():
        raise ExternalOutboxError("external outbox item evidence path or url is required")
    if not str(evidence.get("quote_or_field") or "").strip():
        raise ExternalOutboxError("external outbox item evidence quote_or_field is required")
    if item.get("phase") == "prepared":
        _validate_prepared_binding(item)
    if item.get("phase") == "committed" and not str(item.get("external_effect_id") or "").strip():
        raise ExternalOutboxError("committed external outbox items require external_effect_id")


def _next_outbox_id(items: list[dict[str, Any]]) -> str:
    max_seen = 0
    for item in items:
        raw = str(item.get("outbox_id") or "")
        if raw.startswith("outbox-"):
            try:
                max_seen = max(max_seen, int(raw.removeprefix("outbox-")))
            except ValueError:
                continue
    return f"outbox-{max_seen + 1:04d}"


def _find_item(outbox: dict[str, Any], outbox_id: str) -> dict[str, Any]:
    wanted = (outbox_id or "").strip()
    for item in outbox["items"]:
        if item.get("outbox_id") == wanted or item.get("id") == wanted:
            return item
    raise ExternalOutboxError(f"external outbox item not found: {wanted}")


def _validate_prepared_binding(item: dict[str, Any]) -> None:
    if not str(item.get("approval_request_digest") or "").strip():
        raise ExternalOutboxError("approval_request_digest is required before external dispatch")
    status = str(item.get("approval_status") or "").strip()
    if status not in PREPARED_APPROVAL_STATUSES:
        raise ExternalOutboxError(f"approval_status must be one of: {', '.join(sorted(PREPARED_APPROVAL_STATUSES))}")
    if not str(item.get("decision_id") or item.get("waiver_id") or "").strip():
        raise ExternalOutboxError("decision_id or waiver_id is required before external dispatch")
    if item.get("dispatch_allowed") is not True:
        raise ExternalOutboxError("dispatch_allowed=true is required before external dispatch")


def read_external_outbox(run_dir: str | Path) -> dict[str, Any]:
    """Read RUN_DIR/external-outbox.json, returning an empty v1 outbox if absent."""

    return _load_outbox(Path(run_dir))


def enqueue_external_outbox_item(
    run_dir: str | Path,
    *,
    kind: str,
    target: str,
    action: str,
    evidence_path_or_url: str,
    quote_or_field: str,
    reason: str,
    compensation_capability: str = "manual",
    actor: str = "unknown",
    created_at: str | None = None,
) -> dict[str, Any]:
    """Record an external action intent without dispatching it."""

    run_path = Path(run_dir)
    run_id = _load_run_id(run_path)
    if kind not in ALLOWED_KINDS:
        raise ExternalOutboxError(f"kind must be one of: {', '.join(sorted(ALLOWED_KINDS))}")
    if compensation_capability not in ALLOWED_COMPENSATION_CAPABILITIES:
        raise ExternalOutboxError(
            f"compensation_capability must be one of: {', '.join(sorted(ALLOWED_COMPENSATION_CAPABILITIES))}"
        )
    target_value = (target or "").strip()
    action_value = (action or "").strip()
    reason_value = (reason or "").strip()
    if not target_value:
        raise ExternalOutboxError("target is required")
    if not action_value:
        raise ExternalOutboxError("action is required")
    if not reason_value:
        raise ExternalOutboxError("reason is required")
    _validate_no_sensitive_payload(target_value, action_value, reason_value)

    outbox = _load_outbox(run_path)
    item = {
        "schema_version": OUTBOX_ITEM_SCHEMA_VERSION,
        "outbox_id": _next_outbox_id(outbox["items"]),
        "run_id": run_id,
        "phase": "pending",
        "status": "pending",
        "kind": kind,
        "target": target_value,
        "action": action_value,
        "reason": reason_value,
        "actor": (actor or "").strip() or "unknown",
        "created_at": created_at or _utc_now(),
        "updated_at": created_at or _utc_now(),
        "exact_rollback": False,
        "compensation_capability": compensation_capability,
        "compensation_boundary": "compensatable_not_reversible",
        "compensation_status": "not_started",
        "dispatch_allowed": False,
        "external_execution_by_safeloop": False,
        "network_calls": False,
        "evidence": _evidence(evidence_path_or_url, quote_or_field),
    }
    _validate_item(item)
    outbox["items"].append(item)
    _write_outbox(run_path, outbox)
    return item


def prepare_external_outbox_item(
    run_dir: str | Path,
    outbox_id: str,
    *,
    approval_request_digest: str,
    approval_status: str,
    decision_id: str | None = None,
    waiver_id: str | None = None,
    actor: str = "unknown",
    prepared_at: str | None = None,
) -> dict[str, Any]:
    """Mark a pending external outbox item prepared for dispatch after approval/waiver binding."""

    run_path = Path(run_dir)
    outbox = _load_outbox(run_path)
    item = _find_item(outbox, outbox_id)
    if item.get("phase") not in {"pending", "manual_review"}:
        raise ExternalOutboxError("only pending or manual_review outbox items can be prepared")
    updated = dict(item)
    updated.update(
        {
            "phase": "prepared",
            "status": "prepared",
            "approval_request_digest": (approval_request_digest or "").strip(),
            "approval_status": (approval_status or "").strip(),
            "decision_id": (decision_id or "").strip() or None,
            "waiver_id": (waiver_id or "").strip() or None,
            "dispatch_allowed": True,
            "prepared_at": prepared_at or _utc_now(),
            "prepared_by": (actor or "").strip() or "unknown",
            "updated_at": prepared_at or _utc_now(),
        }
    )
    _validate_item(updated)
    item.clear()
    item.update(updated)
    _write_outbox(run_path, outbox)
    return updated


def commit_external_outbox_item(
    run_dir: str | Path,
    outbox_id: str,
    *,
    external_ref: str,
    evidence_path_or_url: str,
    quote_or_field: str,
    actor: str = "unknown",
    committed_at: str | None = None,
) -> dict[str, Any]:
    """Record a prepared external outbox item as committed and append external-effects.jsonl evidence."""

    run_path = Path(run_dir)
    outbox = _load_outbox(run_path)
    item = _find_item(outbox, outbox_id)
    if item.get("phase") != "prepared" or item.get("dispatch_allowed") is not True:
        raise ExternalOutboxError("external outbox item must be prepared before commit")
    ref = (external_ref or "").strip()
    if not ref:
        raise ExternalOutboxError("external_ref is required")
    _validate_no_sensitive_payload(ref)

    effect = record_external_effect(
        run_path,
        kind=str(item["kind"]),
        target=str(item["target"]),
        action=str(item["action"]),
        evidence_path_or_url=evidence_path_or_url,
        quote_or_field=quote_or_field,
        compensation_capability=str(item.get("compensation_capability") or "manual"),
        status="manual_review_required",
        created_at=committed_at,
        outbox_id=str(item["outbox_id"]),
        lifecycle_phase="committed",
    )
    updated = dict(item)
    updated.update(
        {
            "phase": "committed",
            "status": "committed",
            "external_effect_id": effect["effect_id"],
            "external_ref": ref,
            "commit_evidence": _evidence(evidence_path_or_url, quote_or_field),
            "committed_at": effect["created_at"],
            "committed_by": (actor or "").strip() or "unknown",
            "compensation_status": "manual_review_required",
            "dispatch_allowed": False,
            "dispatch_authorized": True,
            "updated_at": effect["created_at"],
        }
    )
    _validate_item(updated)
    item.clear()
    item.update(updated)
    _write_outbox(run_path, outbox)
    return {"schema_version": "external-outbox-commit-result.v1", "item": updated, "external_effect": effect}
