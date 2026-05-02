from __future__ import annotations

from pathlib import Path

import pytest

from safeloop.control_plane.auth import (
    AccessDenied,
    LocalTokenStore,
    Principal,
    require_permission,
)
from safeloop.control_plane.authorized import record_approval_as, upsert_user_as
from safeloop.control_plane.dashboard import render_static_dashboard
from safeloop.control_plane.registry import ApprovalRecord, ControlPlaneRegistry, RegistryUser
from safeloop.control_plane.signing import sign_approval_record, verify_approval_record


def test_rbac_permissions_by_role_and_local_tokens(tmp_path: Path) -> None:
    store = LocalTokenStore(tmp_path / "tokens.sqlite3")
    admin_token = store.issue_token("ada", "admin")
    operator_token = store.issue_token("ops", "operator")
    viewer_token = store.issue_token("val", "viewer")

    assert admin_token not in store.debug_token_hashes()
    assert store.authenticate(admin_token).role == "admin"

    for permission in (
        "view",
        "request_approval",
        "approve",
        "reject",
        "resume",
        "manage_users",
        "rotate_secrets",
        "export_anchors",
    ):
        require_permission(store.authenticate(admin_token), permission)

    for permission in (
        "request_approval",
        "approve",
        "reject",
        "resume",
        "manage_users",
        "rotate_secrets",
        "export_anchors",
    ):
        with pytest.raises(AccessDenied):
            require_permission(store.authenticate(viewer_token), permission)

    require_permission(store.authenticate(operator_token), "request_approval")
    require_permission(store.authenticate(operator_token), "approve")
    require_permission(store.authenticate(operator_token), "reject")
    require_permission(store.authenticate(operator_token), "resume")
    require_permission(store.authenticate(operator_token), "export_anchors")
    for permission in ("manage_users", "rotate_secrets"):
        with pytest.raises(AccessDenied):
            require_permission(store.authenticate(operator_token), permission)


def test_authorized_control_plane_operations_enforce_rbac(tmp_path: Path) -> None:
    registry = ControlPlaneRegistry(tmp_path / "control-plane.sqlite3")
    registry.initialize()
    admin = Principal("ada", "admin")
    operator = Principal("ops", "operator")
    viewer = Principal("val", "viewer")

    upsert_user_as(registry, admin, RegistryUser("ops", "operator", "Ops"))
    with pytest.raises(AccessDenied):
        upsert_user_as(registry, operator, RegistryUser("val", "viewer", "Val"))

    approval = sign_approval_record(_approval(requested_by="ops"), b"demo-secret")
    record_approval_as(registry, operator, approval)
    with pytest.raises(AccessDenied):
        record_approval_as(registry, viewer, approval)

    assert registry.get_approval("appr-1") == approval


def _approval(**overrides: str) -> ApprovalRecord:
    fields = {
        "approval_id": "appr-1",
        "requested_by": "ops",
        "action": "resume_run",
        "subject": "runs/run-123",
        "status": "pending",
        "signed_payload": "",
        "signature": "",
        "created_at": "2026-05-03T03:00:00Z",
    }
    fields.update(overrides)
    return ApprovalRecord(**fields)  # type: ignore[arg-type]


def test_hmac_approval_record_signatures_detect_tampering_and_wrong_key() -> None:
    signed = sign_approval_record(_approval(), b"demo-secret")

    assert signed.signed_payload
    assert signed.signature.startswith("sha256=")
    assert verify_approval_record(signed, b"demo-secret")
    assert not verify_approval_record(signed, b"wrong-secret")

    for field, value in {
        "action": "delete_run",
        "subject": "runs/other",
        "status": "approved",
        "requested_by": "mallory",
        "created_at": "2026-05-04T03:00:00Z",
        "approval_id": "appr-other",
    }.items():
        tampered = _approval(
            signed_payload=signed.signed_payload,
            signature=signed.signature,
            **{field: value},
        )
        assert not verify_approval_record(tampered, b"demo-secret"), field


def test_static_dashboard_renders_registry_and_escapes_dynamic_fields(tmp_path: Path) -> None:
    registry = ControlPlaneRegistry(tmp_path / "control-plane.sqlite3")
    registry.initialize()
    registry.upsert_user(
        RegistryUser('ada" onclick="alert(1)&', "admin", 'Ada & "Admin"')
    )
    registry.upsert_user(RegistryUser("val", "viewer", "Val"))
    signed = sign_approval_record(
        _approval(
            action='resume" onmouseover="alert(1)&',
            subject='runs/x?arg=1&name="<script>"',
        ),
        b"demo-secret",
    )
    registry.record_approval(signed)

    html = render_static_dashboard(registry)

    assert "SafeLoop Control Plane" in html
    assert "pending" in html
    assert "resume&quot; onmouseover=&quot;alert(1)&amp;" in html
    assert "runs/x?arg=1&amp;name=&quot;&lt;script&gt;&quot;" in html
    assert "Ada &amp; &quot;Admin&quot;" in html
    assert 'onclick="alert(1)' not in html
    assert 'onmouseover="alert(1)' not in html
    assert "<script>" not in html
