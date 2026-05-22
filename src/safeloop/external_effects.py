from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "external-side-effect.v1"
ALLOWED_KINDS = {"github_pr", "github_issue", "message", "email", "webhook", "deploy", "payment", "unknown"}
ALLOWED_COMPENSATION_CAPABILITIES = {"none", "manual", "best_effort", "verified"}
ALLOWED_STATUSES = {
    "recorded",
    "manual_review_required",
    "compensation_planned",
    "compensation_completed",
    "compensation_failed",
    "ignored_by_operator",
}
_SENSITIVE_RE = re.compile(r"(?i)(authorization|bearer\s+[a-z0-9._~+/=-]+|api[_-]?key|secret|token|password)[:= ]")


class ExternalEffectValidationError(ValueError):
    """Raised when an external side-effect record violates SafeLoop boundaries."""


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ExternalEffectValidationError(f"missing run.json in run directory: {path.parent}") from exc
    except json.JSONDecodeError as exc:
        raise ExternalEffectValidationError(f"invalid JSON: {path}") from exc


def _registry_path(run_dir: str | Path) -> Path:
    return Path(run_dir) / "external-effects.jsonl"


def validate_external_effect_record(item: dict[str, Any], *, line_no: int | None = None) -> dict[str, Any]:
    """Validate one external-side-effect.v1 record read from external-effects.jsonl.

    The writer already validates records on append, but consumers also validate
    read-side records so hand-written/corrupt registries fail closed instead of
    being silently normalized in operator-facing artifacts.
    """

    prefix = f"external-effects.jsonl line {line_no}: " if line_no is not None else ""
    if not isinstance(item, dict):
        raise ExternalEffectValidationError(f"{prefix}record must be an object")
    if item.get("schema_version") != SCHEMA_VERSION:
        raise ExternalEffectValidationError(f"{prefix}schema_version must be {SCHEMA_VERSION}")
    for field in ["effect_id", "run_id", "kind", "target", "action", "created_at", "compensation_capability", "status"]:
        if not str(item.get(field) or "").strip():
            raise ExternalEffectValidationError(f"{prefix}{field} is required")
    if item.get("kind") not in ALLOWED_KINDS:
        raise ExternalEffectValidationError(f"{prefix}kind must be one of: {', '.join(sorted(ALLOWED_KINDS))}")
    if item.get("compensation_capability") not in ALLOWED_COMPENSATION_CAPABILITIES:
        raise ExternalEffectValidationError(
            f"{prefix}compensation_capability must be one of: {', '.join(sorted(ALLOWED_COMPENSATION_CAPABILITIES))}"
        )
    if item.get("status") not in ALLOWED_STATUSES:
        raise ExternalEffectValidationError(f"{prefix}status must be one of: {', '.join(sorted(ALLOWED_STATUSES))}")
    if item.get("exact_rollback") is not False:
        raise ExternalEffectValidationError(f"{prefix}exact_rollback must always be false for external side effects")
    evidence = item.get("evidence")
    if not isinstance(evidence, dict):
        raise ExternalEffectValidationError(f"{prefix}evidence object is required")
    evidence_value = str(evidence.get("path") or evidence.get("url") or "").strip()
    quote_value = str(evidence.get("quote_or_field") or "").strip()
    if not evidence_value:
        raise ExternalEffectValidationError(f"{prefix}evidence path or url is required")
    if not quote_value:
        raise ExternalEffectValidationError(f"{prefix}evidence quote_or_field is required")
    _validate_no_sensitive_payload(
        str(item.get("target") or ""),
        str(item.get("action") or ""),
        evidence_value,
        quote_value,
    )
    return item


def read_external_effects(run_dir: str | Path, *, strict: bool = True) -> list[dict[str, Any]]:
    path = _registry_path(run_dir)
    if not path.exists():
        return []
    effects: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ExternalEffectValidationError(f"invalid external-effects.jsonl line {line_no}") from exc
        if not isinstance(item, dict):
            raise ExternalEffectValidationError(f"external-effects.jsonl line {line_no}: record must be an object")
        effects.append(validate_external_effect_record(item, line_no=line_no) if strict else item)
    return effects


def _next_effect_id(run_dir: Path) -> str:
    return f"ext-{len(read_external_effects(run_dir)) + 1:04d}"


def _evidence_key(path_or_url: str) -> str:
    value = path_or_url.strip()
    if value.startswith(("http://", "https://")):
        return "url"
    return "path"


def _validate_no_sensitive_payload(*values: str) -> None:
    for value in values:
        if _SENSITIVE_RE.search(value or ""):
            raise ExternalEffectValidationError("raw sensitive payload storage is not allowed; store a path/url plus a stable quote_or_field only")


def record_external_effect(
    run_dir: str | Path,
    *,
    kind: str,
    target: str,
    action: str,
    evidence_path_or_url: str,
    quote_or_field: str,
    compensation_capability: str = "manual",
    status: str | None = None,
    exact_rollback: bool = False,
    created_at: str | None = None,
    outbox_id: str | None = None,
    lifecycle_phase: str | None = None,
) -> dict[str, Any]:
    """Append one external side-effect record to RUN_DIR/external-effects.jsonl.

    External effects are actions outside the local repo. They are never exact
    rollback; they are tracked for compensation/manual review evidence only.
    """

    run_path = Path(run_dir)
    run = _load_json(run_path / "run.json")
    run_id = str(run.get("run_id") or "").strip()
    if not run_id:
        raise ExternalEffectValidationError("run.json must include run_id")

    kind = (kind or "").strip()
    if kind not in ALLOWED_KINDS:
        raise ExternalEffectValidationError(f"kind must be one of: {', '.join(sorted(ALLOWED_KINDS))}")
    target = (target or "").strip()
    action = (action or "").strip()
    if not target:
        raise ExternalEffectValidationError("target is required")
    if not action:
        raise ExternalEffectValidationError("action is required")
    if exact_rollback is not False:
        raise ExternalEffectValidationError("exact_rollback must always be false for external side effects")

    capability = (compensation_capability or "").strip()
    if capability not in ALLOWED_COMPENSATION_CAPABILITIES:
        raise ExternalEffectValidationError(
            f"compensation_capability must be one of: {', '.join(sorted(ALLOWED_COMPENSATION_CAPABILITIES))}"
        )

    evidence_value = (evidence_path_or_url or "").strip()
    quote_value = (quote_or_field or "").strip()
    if not evidence_value:
        raise ExternalEffectValidationError("evidence path or url is required")
    if not quote_value:
        raise ExternalEffectValidationError("evidence quote_or_field is required")
    _validate_no_sensitive_payload(target, action, evidence_value, quote_value)

    final_status = (status or "manual_review_required").strip()
    if final_status not in ALLOWED_STATUSES:
        raise ExternalEffectValidationError(f"status must be one of: {', '.join(sorted(ALLOWED_STATUSES))}")
    if not evidence_value or not quote_value:
        final_status = "manual_review_required"

    evidence = {_evidence_key(evidence_value): evidence_value, "quote_or_field": quote_value}
    effect = {
        "schema_version": SCHEMA_VERSION,
        "effect_id": _next_effect_id(run_path),
        "run_id": run_id,
        "kind": kind,
        "target": target,
        "action": action,
        "created_at": created_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "exact_rollback": False,
        "compensation_capability": capability,
        "evidence": evidence,
        "status": final_status,
    }
    if outbox_id:
        effect["outbox_id"] = str(outbox_id)
    if lifecycle_phase:
        effect["lifecycle_phase"] = str(lifecycle_phase)

    path = _registry_path(run_path)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(effect, sort_keys=True, separators=(",", ":")) + "\n")
    return effect
