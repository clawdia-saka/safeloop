"""Static HTML renderer for the local SafeLoop control-plane demo."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, is_dataclass
from html import escape
from typing import Any
from urllib.parse import urlparse

from safeloop.control_plane.registry import ControlPlaneRegistry

_REQUIRED_APPROVAL_FIELDS = ("approval_id", "status", "action", "subject")
_WARNING_STATUSES = {"missing", "unknown", "invalid", "unsigned", "unverified"}
_COUNT_STATUSES = ("pending", "approved", "rejected", "expired", "revoked")


def render_static_dashboard(registry: ControlPlaneRegistry) -> str:
    """Render a static, escaped dashboard snapshot for users and approvals.

    This is a local product-demo foundation only: it does not implement hosting,
    sessions, or browser-side authorization.
    """
    users = [_record_to_dict(user) for user in _safe_call(registry, "list_users")]
    approvals = [_normalize_approval(record) for record in _safe_call(registry, "list_approvals")]
    pending_count = sum(1 for approval in approvals if _status_key(approval.get("status")) == "pending")
    return render_static_dashboard_v2(users=users, approvals=approvals, pending_count=pending_count)


def render_static_dashboard_v2(
    *,
    users: Iterable[Any] = (),
    approvals: Iterable[Any] = (),
    pending_count: int | None = None,
) -> str:
    """Render dashboard v2 from adapter-friendly dict/dataclass/object records.

    The renderer deliberately treats missing safety-critical approval fields as a
    warning state instead of inventing a value. This keeps static/demo consumers
    fail-closed while PR B-D APIs are still evolving.
    """
    user_records = [_record_to_dict(user) for user in users]
    approval_records = [_normalize_approval(record) for record in approvals]
    status_counts = _approval_status_counts(approval_records)
    if pending_count is None:
        pending_count = status_counts["pending"]
    else:
        status_counts["pending"] = pending_count

    user_rows = "\n".join(_render_user_row(user) for user in user_records)
    approval_rows = "\n".join(_render_approval_row(approval) for approval in approval_records)
    approval_details = "\n".join(_render_approval_detail(approval) for approval in approval_records)
    warnings = [warning for approval in approval_records for warning in approval["warnings"]]
    warning_block = _render_warnings(warnings)

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>SafeLoop Control Plane</title>
</head>
<body>
  <h1>SafeLoop Control Plane</h1>
  <p>Local static demo snapshot. Pending approvals: {_html(status_counts['pending'])}</p>
  {_render_status_counts(status_counts)}
  {warning_block}
  <h2>Users</h2>
  <table>
    <thead><tr><th>User</th><th>Role</th><th>Display name</th></tr></thead>
    <tbody>{user_rows}</tbody>
  </table>
  <h2>Approvals</h2>
  <table>
    <thead><tr><th>ID</th><th>Status</th><th>Operator</th><th>Action</th><th>Subject</th><th>Signature</th><th>Anchor</th><th>Created</th></tr></thead>
    <tbody>{approval_rows}</tbody>
  </table>
  <h2>Approval details</h2>
  {approval_details}
</body>
</html>"""


def _safe_call(obj: Any, method_name: str) -> list[Any]:
    method = getattr(obj, method_name)
    try:
        return list(method())
    except Exception as exc:
        if method_name == "list_approvals":
            return [
                {
                    "approval_id": "registry-error",
                    "status": "unknown",
                    "action": "registry-error",
                    "subject": "control-plane registry",
                    "warnings": [f"registry-error: {method_name} failed: {exc}; render-only controls disabled"],
                }
            ]
        return []


def _record_to_dict(record: Any) -> dict[str, Any]:
    if record is None:
        return {}
    if isinstance(record, Mapping):
        return dict(record)
    if is_dataclass(record):
        return asdict(record)
    return {
        name: getattr(record, name)
        for name in dir(record)
        if not name.startswith("_") and not callable(getattr(record, name, None))
    }


def _normalize_approval(record: Any) -> dict[str, Any]:
    data = _record_to_dict(record)
    if "id" in data and "approval_id" not in data:
        data["approval_id"] = data["id"]
    if "operator" not in data:
        data["operator"] = data.get("requested_by") or data.get("requestedBy")
    if "anchor" not in data:
        data["anchor"] = data.get("anchor_id") or data.get("ledger_anchor") or data.get("journal_anchor")
    warnings: list[str] = []
    approval_id = data.get("approval_id") or "unknown approval"
    for field in _REQUIRED_APPROVAL_FIELDS:
        if not data.get(field):
            warnings.append(f"{approval_id}: missing {field}; render-only controls disabled")
    status = _status_key(data.get("status")) or "missing"
    if status in _WARNING_STATUSES:
        warnings.append(f"{approval_id}: status is {status}; treat as not actionable")
    if "warnings" in data:
        warnings.extend(_as_list(data["warnings"]))
    if not data.get("signature") or not data.get("signed_payload"):
        warnings.append(f"{approval_id}: missing signature; treat as unverified")
    data["warnings"] = warnings
    return data


def _status_key(status: Any) -> str:
    status_text = str(status or "").strip().lower()
    return "pending" if status_text == "requested" else status_text


def _approval_status_counts(approvals: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    counts = {status: 0 for status in _COUNT_STATUSES}
    for approval in approvals:
        status = _status_key(approval.get("status"))
        if status in counts:
            counts[status] += 1
    return counts


def _render_user_row(user: Mapping[str, Any]) -> str:
    return (
        "<tr>"
        f"<td>{_html(user.get('user_id'))}</td>"
        f"<td>{_html(user.get('role'))}</td>"
        f"<td>{_html(user.get('display_name'))}</td>"
        "</tr>"
    )


def _render_approval_row(approval: Mapping[str, Any]) -> str:
    row_class = ' class="warning"' if approval.get("warnings") else ""
    return (
        f"<tr{row_class}>"
        f"<td>{_html(approval.get('approval_id'))}</td>"
        f"<td>{_html(approval.get('status'))}</td>"
        f"<td>{_html(approval.get('operator'))}</td>"
        f"<td>{_html(approval.get('action'))}</td>"
        f"<td>{_html(approval.get('subject'))}</td>"
        f"<td>{_html(approval.get('signature'))}</td>"
        f"<td>{_html(approval.get('anchor'))}</td>"
        f"<td>{_html(approval.get('created_at') or approval.get('createdAt'))}</td>"
        "</tr>"
    )


def _render_approval_detail(approval: Mapping[str, Any]) -> str:
    fields = (
        "approval_id",
        "status",
        "operator",
        "action",
        "subject",
        "signature",
        "anchor",
        "approval_evidence",
        "run_evidence",
        "checkpoint_evidence",
        "artifact_evidence",
        "rollback_tier",
        "side_effect_status",
        "anchor_verification_status",
    )
    items = "\n".join(
        f"    <li><strong>{_html(field)}</strong>: {_html(approval.get(field))}</li>" for field in fields
    )
    links = _render_evidence_links(approval)
    rollback_blockers = _render_value_list("Rollback blockers", approval.get("rollback_blockers"))
    why_blocked = _render_value_list("Why blocked", approval.get("why_blocked") or approval.get("why_blocked_reasons"))
    warnings = "\n".join(f"    <li>{_html(warning)}</li>" for warning in approval.get("warnings", []))
    warning_section = f"\n  <p>Fail-closed warnings:</p>\n  <ul>\n{warnings}\n  </ul>" if warnings else ""
    return f"""<section class="approval-detail">
  <h3>Approval {_html(approval.get('approval_id'))}</h3>
  <ul>
{items}
  </ul>{links}{rollback_blockers}{why_blocked}{warning_section}
</section>"""


def _render_status_counts(status_counts: Mapping[str, int]) -> str:
    items = "\n".join(
        f"    <li>{_html(status.title())} approvals: {_html(status_counts.get(status, 0))}</li>"
        for status in _COUNT_STATUSES
    )
    return f"""<section aria-label="Approval status counts">
  <h2>Approval status counts</h2>
  <ul>
{items}
  </ul>
</section>"""


def _render_evidence_links(approval: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for label in ("approval", "run", "checkpoint", "artifact"):
        url = approval.get(f"{label}_url") or approval.get(f"{label}_link") or approval.get(f"{label}_href")
        if not url:
            continue
        if _safe_href(url):
            parts.append(f'<li><a href="{_html(url)}">{_html(label)}</a></li>')
        else:
            parts.append(f"<li>blocked unsafe {_html(label)} link: {_html(url)}</li>")
    if not parts:
        return ""
    return "\n  <p>Evidence links:</p>\n  <ul>\n    " + "\n    ".join(parts) + "\n  </ul>"


def _safe_href(url: Any) -> bool:
    parsed = urlparse(str(url).strip())
    return parsed.scheme in {"", "http", "https", "file"} and not (parsed.scheme == "" and parsed.netloc)


def _render_value_list(label: str, value: Any) -> str:
    values = _as_list(value)
    if not values:
        return ""
    items = "\n".join(f"    <li>{_html(item)}</li>" for item in values)
    return f"\n  <p>{_html(label)}:</p>\n  <ul>\n{items}\n  </ul>"


def _as_list(value: Any) -> list[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray, Mapping)):
        return list(value)
    return [value]


def _render_warnings(warnings: list[str]) -> str:
    if not warnings:
        return "<p>No fail-closed warnings.</p>"
    items = "\n".join(f"    <li>{_html(warning)}</li>" for warning in warnings)
    return f"""<section aria-label="Fail-closed warnings">
  <h2>Fail-closed warnings</h2>
  <ul>
{items}
  </ul>
</section>"""


def _html(value: Any) -> str:
    return escape("" if value is None else str(value), quote=True)
