"""Static HTML renderer for the local SafeLoop control-plane demo."""

from __future__ import annotations

from html import escape

from safeloop.control_plane.registry import ControlPlaneRegistry


def render_static_dashboard(registry: ControlPlaneRegistry) -> str:
    """Render a static, escaped dashboard snapshot for users and approvals.

    This is a local product-demo foundation only: it does not implement hosting,
    sessions, or browser-side authorization.
    """
    user_rows = "\n".join(
        "<tr>"
        f"<td>{escape(user.user_id)}</td>"
        f"<td>{escape(user.role)}</td>"
        f"<td>{escape(user.display_name or '')}</td>"
        "</tr>"
        for user in registry.list_users()
    )
    approval_rows = "\n".join(
        "<tr>"
        f"<td>{escape(approval.approval_id)}</td>"
        f"<td>{escape(approval.status)}</td>"
        f"<td>{escape(approval.requested_by)}</td>"
        f"<td>{escape(approval.action)}</td>"
        f"<td>{escape(approval.subject)}</td>"
        f"<td>{escape(approval.created_at)}</td>"
        "</tr>"
        for approval in registry.list_approvals()
    )
    pending_count = len(registry.list_approvals(status="pending"))
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>SafeLoop Control Plane</title>
</head>
<body>
  <h1>SafeLoop Control Plane</h1>
  <p>Local static demo snapshot. Pending approvals: {pending_count}</p>
  <h2>Users</h2>
  <table>
    <thead><tr><th>User</th><th>Role</th><th>Display name</th></tr></thead>
    <tbody>{user_rows}</tbody>
  </table>
  <h2>Approvals</h2>
  <table>
    <thead><tr><th>ID</th><th>Status</th><th>Requested by</th><th>Action</th><th>Subject / evidence path</th><th>Created</th></tr></thead>
    <tbody>{approval_rows}</tbody>
  </table>
</body>
</html>"""
