# Control-plane local demo

This demo exercises the SafeLoop 0.1.0 control-plane MVP without starting a service or touching watchdog runtime artifacts.

## Prerequisites

From the repository root:

```bash
python -m pip install -e .
```

## Run the demo in a Python shell

```python
from pathlib import Path
from safeloop.control_plane.auth import LocalTokenStore, require_permission
from safeloop.control_plane.dashboard import render_static_dashboard
from safeloop.control_plane.registry import ApprovalRecord, ControlPlaneRegistry, RegistryUser
from safeloop.control_plane.signing import sign_approval_record, verify_approval_record

root = Path(".demo-control-plane")
registry = ControlPlaneRegistry(root / "control-plane.sqlite3")
registry.initialize()

registry.upsert_user(RegistryUser("ada", "admin", "Ada Admin"))
registry.upsert_user(RegistryUser("ops", "operator", "Ops Lead"))
registry.upsert_user(RegistryUser("val", "viewer", "Val Viewer"))

tokens = LocalTokenStore(root / "tokens.sqlite3")
admin_token = tokens.issue_token("ada", "admin")
operator_token = tokens.issue_token("ops", "operator")
viewer_token = tokens.issue_token("val", "viewer")

admin = tokens.authenticate(admin_token)
operator = tokens.authenticate(operator_token)
viewer = tokens.authenticate(viewer_token)

require_permission(admin, "manage_users")
require_permission(operator, "approve")
require_permission(viewer, "view")

approval = ApprovalRecord(
    approval_id="appr-local-demo-1",
    requested_by="ops",
    action="resume_run",
    subject="runs/run-123",
    status="pending",
    signed_payload="",
    signature="",
    created_at="2026-05-03T03:00:00Z",
)
signed = sign_approval_record(approval, b"replace-me-local-demo-secret")
assert verify_approval_record(signed, b"replace-me-local-demo-secret")
registry.record_approval(signed)

html = render_static_dashboard(registry)
(root / "dashboard.html").write_text(html, encoding="utf-8")
print(root / "dashboard.html")
```

Open `.demo-control-plane/dashboard.html` in a browser to inspect the static snapshot.

## What this demo proves

- The registry is a standalone SQLite database selected by the caller.
- Demo tokens authenticate to principals without storing raw token values.
- RBAC permissions are enforced by helper functions.
- Approval records can be HMAC-signed and verified.
- Dashboard output is static HTML with escaped registry values.

## What this demo does not prove

- It does not start a hosted control-plane service.
- It does not perform approval lifecycle transitions.
- It does not export external anchors.
- It does not integrate approvals with actual watchdog resume/rollback commands.
- It does not provide dashboard v2, authentication, or browser sessions.
