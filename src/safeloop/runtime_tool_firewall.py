"""Runtime tool firewall routing for risky agent tool requests.

The firewall records a default route before a tool crosses a mutation boundary:
local destructive/mutation intents go to quarantine, external write intents go
to the external outbox, and unknown intents become manual-review items.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from safeloop.external_effects import ALLOWED_KINDS
from safeloop.external_outbox import ExternalOutboxError, enqueue_external_outbox_item
from safeloop.quarantine import QuarantineError, put_directory_in_quarantine, put_file_in_quarantine

FIREWALL_LOG = "runtime-tool-firewall.jsonl"
SCHEMA_VERSION = "runtime-tool-firewall-route.v1"
TargetKind = Literal["auto", "local_file", "local_directory", "external", "unknown"]
Route = Literal["allow_read_only", "quarantine", "external_outbox", "manual_review"]

_SENSITIVE_RE = re.compile(r"(?i)(authorization|bearer\s+[a-z0-9._~+/=-]+|api[_-]?key|secret|token|password)[:= ]")
_URL_RE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_READ_ONLY_TERMS = {
    "cat",
    "diff",
    "find",
    "grep",
    "head",
    "inspect",
    "less",
    "list",
    "ls",
    "open",
    "pwd",
    "read",
    "rg",
    "search",
    "show",
    "stat",
    "tail",
    "view",
}
_LOCAL_MUTATION_TERMS = {
    "append",
    "chmod",
    "chown",
    "delete",
    "edit",
    "move",
    "mv",
    "patch",
    "remove",
    "rename",
    "rm",
    "rmdir",
    "shred",
    "truncate",
    "unlink",
    "write",
}
_EXTERNAL_TERMS = {
    "charge",
    "comment",
    "deploy",
    "dispatch",
    "email",
    "github",
    "issue",
    "message",
    "payment",
    "post",
    "pr",
    "publish",
    "push",
    "release",
    "send",
    "slack",
    "telegram",
    "upload",
    "webhook",
}
_TARGET_KINDS = {"auto", "local_file", "local_directory", "external", "unknown"}


class RuntimeToolFirewallError(ValueError):
    """Raised when a firewall route cannot be recorded safely."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _load_run_id(run_path: Path) -> str:
    try:
        data = json.loads((run_path / "run.json").read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeToolFirewallError(f"missing run.json in run directory: {run_path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeToolFirewallError(f"invalid run.json in run directory: {run_path}") from exc
    run_id = str(data.get("run_id") or "").strip()
    if not run_id:
        raise RuntimeToolFirewallError("run.json must include run_id")
    return run_id


def _contains_sensitive_payload(*values: str) -> bool:
    return any(_SENSITIVE_RE.search(value or "") for value in values)


def _require_safe_references(*values: str) -> None:
    if _contains_sensitive_payload(*values):
        raise RuntimeToolFirewallError("raw sensitive payload storage is not allowed; store narrow references only")


def _tokens(tool: str, action: str) -> set[str]:
    raw = f"{tool} {action}".lower()
    return {part for part in re.split(r"[^a-z0-9_+-]+", raw) if part}


def _looks_external_target(target: str) -> bool:
    value = target.strip()
    return bool(_URL_RE.match(value) or value.startswith("mailto:") or _EMAIL_RE.match(value))


def _classify_external_kind(tool: str, action: str, target: str) -> str:
    blob = f"{tool} {action} {target}".lower()
    if "payment" in blob or "charge" in blob or "stripe" in blob:
        return "payment"
    if "email" in blob or "mail" in blob or target.startswith("mailto:") or _EMAIL_RE.match(target.strip()):
        return "email"
    if any(term in blob for term in ["slack", "telegram", "discord", "sms", "message"]):
        return "message"
    if any(term in blob for term in ["pull_request", "pull-request", "github_pr"]) or re.search(r"\bpr\b", blob):
        return "github_pr"
    if "issue" in blob or "github_issue" in blob:
        return "github_issue"
    if any(term in blob for term in ["deploy", "release", "publish", "upload", "push"]):
        return "deploy"
    if "webhook" in blob or _looks_external_target(target):
        return "webhook"
    return "unknown"


def _classify_route(tool: str, action: str, target: str, target_kind: TargetKind) -> tuple[Route, str]:
    terms = _tokens(tool, action)
    if target_kind in {"local_file", "local_directory"}:
        if terms & _LOCAL_MUTATION_TERMS:
            return "quarantine", "explicit local mutation target"
        return "manual_review", "explicit local target without recognized mutation semantics"
    if target_kind == "external":
        return "external_outbox", "explicit external target"
    if target_kind == "unknown":
        return "manual_review", "explicit unknown target"
    if _looks_external_target(target) or terms & _EXTERNAL_TERMS:
        return "external_outbox", "external write/send/publish intent"
    if terms & _LOCAL_MUTATION_TERMS:
        return "quarantine", "destructive or local mutation intent"
    if terms and terms <= _READ_ONLY_TERMS:
        return "allow_read_only", "recognized read-only intent"
    if terms & _READ_ONLY_TERMS and not (terms & (_LOCAL_MUTATION_TERMS | _EXTERNAL_TERMS)):
        return "allow_read_only", "recognized read-only intent"
    return "manual_review", "unrecognized tool semantics"


def _next_event_id(path: Path) -> str:
    count = 0
    if path.exists():
        with path.open(encoding="utf-8") as handle:
            count = sum(1 for line in handle if line.strip())
    return f"fw-{count + 1:04d}"


def _append_firewall_event(run_path: Path, record: dict[str, Any]) -> dict[str, Any]:
    path = run_path / FIREWALL_LOG
    record = dict(record)
    record["event_id"] = _next_event_id(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
    return record


def _base_record(
    *,
    run_id: str,
    tool: str,
    action: str,
    target: str,
    target_kind: TargetKind,
    actor: str,
    reason: str,
    route: Route,
    route_reason: str,
) -> dict[str, Any]:
    manual_review_required = route == "manual_review"
    return {
        "schema_version": SCHEMA_VERSION,
        "timestamp": _utc_now(),
        "run_id": run_id,
        "tool": tool,
        "action": action,
        "target": target,
        "target_kind": target_kind,
        "actor": actor or "unknown",
        "reason": reason,
        "route": route,
        "route_reason": route_reason,
        "default_route": True,
        "manual_review_required": manual_review_required,
        "exact_rollback": route == "quarantine",
        "external_dispatch_allowed": False,
    }


def _route_quarantine(
    run_path: Path,
    *,
    target: str,
    target_kind: TargetKind,
    workspace_root: Path,
    reason: str,
    actor: str,
) -> dict[str, Any]:
    candidate = Path(target)
    resolved = candidate if candidate.is_absolute() else workspace_root / candidate
    if target_kind == "local_directory" or resolved.is_dir():
        item = put_directory_in_quarantine(
            target,
            run_dir=run_path,
            workspace_root=workspace_root,
            reason=reason,
            actor=actor,
        )
    else:
        item = put_file_in_quarantine(
            target,
            run_dir=run_path,
            workspace_root=workspace_root,
            reason=reason,
            actor=actor,
        )
    return {
        "quarantine_item_id": item["item_id"],
        "quarantine_action": item["action"],
        "quarantine_original_path": item["original_path"],
        "restore_supported": bool(item.get("restore_supported")),
        "artifacts": [
            f"quarantine/items/{item['item_id']}/item.json",
            f"quarantine/items/{item['item_id']}/restore-manifest.json",
            f"quarantine/items/{item['item_id']}/audit.jsonl",
        ],
    }


def _route_external_outbox(
    run_path: Path,
    *,
    tool: str,
    action: str,
    target: str,
    reason: str,
    actor: str,
) -> dict[str, Any]:
    kind = _classify_external_kind(tool, action, target)
    if kind not in ALLOWED_KINDS:
        kind = "unknown"
    item = enqueue_external_outbox_item(
        run_path,
        kind=kind,
        target=target,
        action=action or tool,
        evidence_path_or_url=FIREWALL_LOG,
        quote_or_field=f"runtime_tool_firewall: tool={tool}; action={action or tool}",
        reason=reason,
        compensation_capability="manual",
        actor=actor,
    )
    return {
        "outbox_id": item["outbox_id"],
        "external_kind": item["kind"],
        "outbox_phase": item["phase"],
        "dispatch_allowed": item["dispatch_allowed"],
        "exact_rollback": False,
        "artifacts": ["external-outbox.json"],
    }


def route_tool_action(
    run_dir: str | Path,
    *,
    tool: str,
    action: str,
    target: str,
    workspace_root: str | Path = ".",
    reason: str,
    actor: str = "unknown",
    target_kind: TargetKind = "auto",
) -> dict[str, Any]:
    """Route a tool request to quarantine, external outbox, or manual review.

    This function records intent and safety routing only. It never dispatches
    external calls and it never executes arbitrary tools.
    """

    run_path = Path(run_dir)
    tool_value = (tool or "").strip()
    action_value = (action or "").strip()
    target_value = (target or "").strip()
    reason_value = (reason or "").strip()
    actor_value = (actor or "").strip() or "unknown"
    kind_value = (target_kind or "auto").strip()
    if kind_value not in _TARGET_KINDS:
        raise RuntimeToolFirewallError(f"target_kind must be one of: {', '.join(sorted(_TARGET_KINDS))}")
    if not tool_value:
        raise RuntimeToolFirewallError("tool is required")
    if not action_value:
        raise RuntimeToolFirewallError("action is required")
    if not target_value:
        raise RuntimeToolFirewallError("target is required")
    if not reason_value:
        raise RuntimeToolFirewallError("reason is required")
    _require_safe_references(tool_value, action_value, target_value, reason_value)

    run_id = _load_run_id(run_path)
    route, route_reason = _classify_route(tool_value, action_value, target_value, kind_value)  # type: ignore[arg-type]
    record = _base_record(
        run_id=run_id,
        tool=tool_value,
        action=action_value,
        target=target_value,
        target_kind=kind_value,  # type: ignore[arg-type]
        actor=actor_value,
        reason=reason_value,
        route=route,
        route_reason=route_reason,
    )

    if route == "quarantine":
        try:
            record.update(
                _route_quarantine(
                    run_path,
                    target=target_value,
                    target_kind=kind_value,  # type: ignore[arg-type]
                    workspace_root=Path(workspace_root).resolve(),
                    reason=reason_value,
                    actor=actor_value,
                )
            )
        except QuarantineError as exc:
            record["route"] = "manual_review"
            record["route_reason"] = f"quarantine_unavailable: {exc}"
            record["manual_review_required"] = True
            record["exact_rollback"] = False
            record["external_dispatch_allowed"] = False
    elif route == "external_outbox":
        try:
            record.update(
                _route_external_outbox(
                    run_path,
                    tool=tool_value,
                    action=action_value,
                    target=target_value,
                    reason=reason_value,
                    actor=actor_value,
                )
            )
        except ExternalOutboxError as exc:
            record["route"] = "manual_review"
            record["route_reason"] = f"external_outbox_unavailable: {exc}"
            record["manual_review_required"] = True
            record["exact_rollback"] = False
            record["external_dispatch_allowed"] = False

    record.setdefault("artifacts", [])
    record["artifacts"] = sorted(dict.fromkeys([FIREWALL_LOG, *record["artifacts"]]))
    return _append_firewall_event(run_path, record)


def read_runtime_tool_firewall_events(run_dir: str | Path) -> list[dict[str, Any]]:
    """Read runtime-tool-firewall.jsonl events without normalizing semantics."""

    path = Path(run_dir) / FIREWALL_LOG
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeToolFirewallError(f"invalid {FIREWALL_LOG} line {line_no}") from exc
            if not isinstance(item, dict):
                raise RuntimeToolFirewallError(f"{FIREWALL_LOG} line {line_no}: record must be an object")
            events.append(item)
    return events
