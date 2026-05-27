"""Runtime tool firewall routing for risky agent tool requests.

The firewall records a default route before a tool crosses a mutation boundary:
local destructive/mutation intents go to quarantine, external write intents go
to the external outbox, and unknown intents become manual-review items.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from safeloop.external_effects import ALLOWED_KINDS
from safeloop.external_outbox import ExternalOutboxError, enqueue_external_outbox_item
from safeloop.quarantine import QuarantineError, put_directory_in_quarantine, put_file_in_quarantine
from safeloop.runtime_tool_policy import DEFAULT_POLICY_PROFILE, RuntimeToolPolicyError, resolve_policy_route
from safeloop.storage import exclusive_lock

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
    "copy",
    "cp",
    "delete",
    "edit",
    "mkdir",
    "move",
    "mv",
    "patch",
    "remove",
    "rename",
    "rm",
    "rmdir",
    "shred",
    "touch",
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


def _event_hash(record: dict[str, Any]) -> str:
    payload = dict(record)
    payload.pop("event_hash", None)
    data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _is_sha(value: Any) -> bool:
    return isinstance(value, str) and value.startswith("sha256:") and len(value) == 71


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


def _read_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    previous_hash: str | None = None
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
            event_hash = item.get("event_hash")
            if item.get("prev_event_hash") != previous_hash:
                raise RuntimeToolFirewallError(f"{FIREWALL_LOG} line {line_no}: prev_event_hash mismatch")
            if not _is_sha(event_hash):
                raise RuntimeToolFirewallError(f"{FIREWALL_LOG} line {line_no}: malformed event_hash")
            if _event_hash(item) != event_hash:
                raise RuntimeToolFirewallError(f"{FIREWALL_LOG} line {line_no}: event_hash mismatch")
            previous_hash = event_hash
            events.append(item)
    return events


def _append_firewall_event(run_path: Path, record: dict[str, Any]) -> dict[str, Any]:
    path = run_path / FIREWALL_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+", encoding="utf-8") as lock_handle:
        with exclusive_lock(lock_handle):
            events = _read_events(path)
            record = dict(record)
            record["event_id"] = f"fw-{len(events) + 1:04d}"
            record["prev_event_hash"] = events[-1]["event_hash"] if events else None
            record["event_hash"] = _event_hash(record)
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
    source: str,
    policy_profile: str,
    action_id: str | None = None,
) -> dict[str, Any]:
    manual_review_required = route == "manual_review"
    record = {
        "schema_version": SCHEMA_VERSION,
        "timestamp": _utc_now(),
        "run_id": run_id,
        "source": source,
        "tool": tool,
        "action": action,
        "target": target,
        "target_kind": target_kind,
        "actor": actor or "unknown",
        "reason": reason,
        "route": route,
        "route_reason": route_reason,
        "policy_profile": policy_profile,
        "default_route": True,
        "dry_run": False,
        "manual_review_required": manual_review_required,
        "exact_rollback": route == "quarantine",
        "external_dispatch_allowed": False,
    }
    if action_id:
        record["action_id"] = action_id
    return record


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
    dry_run: bool = False,
    source: str = "api",
    action_id: str | None = None,
    policy_profile: str | None = DEFAULT_POLICY_PROFILE,
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
    source_value = (source or "").strip() or "api"
    action_id_value = (action_id or "").strip() or None
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
    try:
        default_route, default_reason = _classify_route(tool_value, action_value, target_value, kind_value)  # type: ignore[arg-type]
        route, route_reason, profile_value = resolve_policy_route(
            policy_profile=policy_profile,
            tool=tool_value,
            action=action_value,
            default_route=default_route,
            default_reason=default_reason,
        )
    except RuntimeToolPolicyError as exc:
        raise RuntimeToolFirewallError(str(exc)) from exc
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
        source=source_value,
        policy_profile=profile_value,
        action_id=action_id_value,
    )
    if dry_run:
        record["dry_run"] = True
        record["exact_rollback"] = False
        record["artifacts"] = []
        if route == "quarantine":
            record["would_create_artifacts"] = ["quarantine"]
        elif route == "external_outbox":
            record["would_create_artifacts"] = ["external-outbox.json"]
        elif route == "manual_review":
            record["would_create_artifacts"] = [FIREWALL_LOG]
        else:
            record["would_create_artifacts"] = []
        return record

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


def _env_run_dir() -> Path:
    run_dir = os.environ.get("SAFELOOP_RUN_DIR")
    if not run_dir:
        raise RuntimeToolFirewallError("run_dir is required when SAFELOOP_RUN_DIR is not set")
    return Path(run_dir)


def _validate_env_run_binding(run_path: Path) -> None:
    expected_run_id = os.environ.get("SAFELOOP_RUN_ID")
    if not expected_run_id:
        raise RuntimeToolFirewallError("SAFELOOP_RUN_ID is required when run_dir is omitted")
    actual_run_id = _load_run_id(run_path)
    if actual_run_id != expected_run_id:
        raise RuntimeToolFirewallError(
            f"SAFELOOP_RUN_ID mismatch for {run_path}: env={expected_run_id!r} run.json={actual_run_id!r}"
        )


def firewall_preflight(
    *,
    tool: str,
    action: str,
    target: str,
    reason: str,
    run_dir: str | Path | None = None,
    workspace_root: str | Path = ".",
    actor: str = "unknown",
    target_kind: TargetKind = "auto",
    dry_run: bool = False,
    strict: bool = False,
    action_id: str | None = None,
    source: str = "runtime_helper",
    policy_profile: str | None = None,
) -> dict[str, Any]:
    """Classify and record a runtime tool request before execution.

    The helper never executes the requested tool. When ``run_dir`` is omitted,
    it binds to the active SafeLoop run via ``SAFELOOP_RUN_DIR`` and verifies
    ``SAFELOOP_RUN_ID`` against ``run.json``. When called inside
    ``action_span()``, the current ``SAFELOOP_ACTION_ID`` is attached to the
    firewall event unless an explicit ``action_id`` is provided.
    """

    run_path = Path(run_dir) if run_dir is not None else _env_run_dir()
    if run_dir is None:
        _validate_env_run_binding(run_path)
    effective_action_id = (action_id or "").strip() or os.environ.get("SAFELOOP_ACTION_ID") or None
    effective_policy_profile = (policy_profile or "").strip() or os.environ.get("SAFELOOP_POLICY_PROFILE") or DEFAULT_POLICY_PROFILE
    event = route_tool_action(
        run_path,
        tool=tool,
        action=action,
        target=target,
        workspace_root=workspace_root,
        reason=reason,
        actor=actor,
        target_kind=target_kind,
        dry_run=dry_run,
        source=source,
        action_id=effective_action_id,
        policy_profile=effective_policy_profile,
    )
    if strict and event.get("route") == "manual_review":
        route_reason = event.get("route_reason") or "manual_review"
        raise RuntimeToolFirewallError(
            f"runtime tool firewall preflight requires manual review: {route_reason}"
        )
    return event


def read_runtime_tool_firewall_events(run_dir: str | Path) -> list[dict[str, Any]]:
    """Read runtime-tool-firewall.jsonl events without normalizing semantics."""

    path = Path(run_dir) / FIREWALL_LOG
    if not path.exists():
        return []
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+", encoding="utf-8") as lock_handle:
        with exclusive_lock(lock_handle):
            return _read_events(path)
