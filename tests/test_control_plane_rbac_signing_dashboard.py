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
from safeloop.control_plane.dashboard import render_static_dashboard, render_static_dashboard_v2
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


def test_static_dashboard_v2_renders_adapter_dict_fields_and_fail_closed_warnings() -> None:
    html = render_static_dashboard_v2(
        users=[{"user_id": "viewer", "role": "viewer", "display_name": "View & Only"}],
        approvals=[
            {
                "id": "appr-dict",
                "status": "pending",
                "operator": "ops-1",
                "action": "resume_run",
                "subject": "runs/run-123",
                "signature": "sha256=abc123",
                "anchor_id": "ledger:42",
                "createdAt": "2026-05-03T04:00:00Z",
            },
            {
                "approval_id": "appr-missing",
                "operator": "ops-2",
                "subject": "runs/run-456",
            },
        ],
    )

    assert "appr-dict" in html
    assert "ops-1" in html
    assert "sha256=abc123" in html
    assert "ledger:42" in html
    assert "Pending approvals: 1" in html
    assert "appr-missing: missing status; render-only controls disabled" in html
    assert "appr-missing: missing signature; treat as unverified" in html
    assert "Fail-closed warnings" in html


def test_static_dashboard_v2_operator_snapshot_counts_links_evidence_and_blockers() -> None:
    html = render_static_dashboard_v2(
        approvals=[
            {
                "approval_id": "appr-pending",
                "status": "pending",
                "operator": "ops",
                "action": "resume_run",
                "subject": "runs/run-123",
                "signature": "sha256=ok",
                "approval_url": "file:///safe/approvals/appr-pending.json",
                "run_url": "https://example.invalid/runs/run-123?x=1&y=2",
                "checkpoint_url": "file:///safe/checkpoints/ckpt-9.json",
                "artifact_url": "file:///safe/artifacts/art-7.tgz",
                "approval_evidence": "signed by ops",
                "run_evidence": "run ledger row 123",
                "checkpoint_evidence": "ckpt hash abc",
                "artifact_evidence": "artifact sha256 def",
                "rollback_tier": "tier-2",
                "rollback_blockers": ["pending migration", "open tickets"],
                "side_effect_status": "dry-run only",
                "anchor_verification_status": "verified",
                "why_blocked": ["awaiting approver", "business hours only"],
            },
            {"approval_id": "appr-approved", "status": "approved", "action": "resume_run", "subject": "runs/a", "signature": "sha256=a"},
            {"approval_id": "appr-rejected", "status": "rejected", "action": "resume_run", "subject": "runs/r", "signature": "sha256=r"},
            {"approval_id": "appr-expired", "status": "EXPIRED", "action": "resume_run", "subject": "runs/e", "signature": "sha256=e"},
            {"approval_id": "appr-revoked", "status": "REVOKED", "action": "resume_run", "subject": "runs/v", "signature": "sha256=v"},
        ]
    )

    assert "Pending approvals: 1" in html
    assert "Approved approvals: 1" in html
    assert "Rejected approvals: 1" in html
    assert "Expired approvals: 1" in html
    assert "Revoked approvals: 1" in html
    assert '<a href="file:///safe/approvals/appr-pending.json">approval</a>' in html
    assert '<a href="https://example.invalid/runs/run-123?x=1&amp;y=2">run</a>' in html
    assert '<a href="file:///safe/checkpoints/ckpt-9.json">checkpoint</a>' in html
    assert '<a href="file:///safe/artifacts/art-7.tgz">artifact</a>' in html
    assert "signed by ops" in html
    assert "run ledger row 123" in html
    assert "ckpt hash abc" in html
    assert "artifact sha256 def" in html
    assert "tier-2" in html
    assert "pending migration" in html
    assert "open tickets" in html
    assert "dry-run only" in html
    assert "verified" in html
    assert "awaiting approver" in html
    assert "business hours only" in html


def test_static_dashboard_v2_escapes_xss_payloads_in_list_and_detail() -> None:
    payload = '<img src=x onerror="alert(1)"><script>alert(2)</script>'
    html = render_static_dashboard_v2(
        users=[{"user_id": payload, "role": "viewer", "display_name": payload}],
        approvals=[
            {
                "approval_id": payload,
                "status": "pending",
                "operator": payload,
                "action": payload,
                "subject": payload,
                "signature": "sha256=<bad>",
                "anchor": payload,
            }
        ],
    )

    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in html
    assert "sha256=&lt;bad&gt;" in html
    assert "<script>" not in html
    assert "<img src=x" not in html
    assert 'onerror="alert(1)"' not in html



def test_static_dashboard_v2_blocks_dangerous_evidence_link_schemes() -> None:
    html = render_static_dashboard_v2(
        approvals=[
            {
                "approval_id": "appr-js-link",
                "status": "approved",
                "action": "resume_run",
                "subject": "runs/run-123",
                "signature": "sha256=ok",
                "approval_url": "javascript:alert(1)",
                "run_url": "data:text/html,<script>alert(1)</script>",
                "artifact_url": "https://example.invalid/artifact.json",
            }
        ]
    )

    assert 'href="javascript:alert(1)"' not in html
    assert 'href="data:text/html' not in html
    assert "blocked unsafe approval link" in html
    assert "blocked unsafe run link" in html
    assert '<a href="https://example.invalid/artifact.json">artifact</a>' in html
