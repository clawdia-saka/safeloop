from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from safeloop.control_plane.gate import ApprovalGateError, require_approval
from safeloop.control_plane.lifecycle import ApprovalLifecycleStore
from safeloop.control_plane.registry import ApprovalRecord, ControlPlaneRegistry
from safeloop.control_plane.signing import sign_approval_record

KEY = b"runtime-gate-test-key"
NOW = datetime(2026, 5, 3, 5, 0, tzinfo=timezone.utc)


def _record(**overrides: str) -> ApprovalRecord:
    fields = {
        "approval_id": "appr-1",
        "requested_by": "ops",
        "action": "resume_run",
        "subject": "runs/run-123",
        "status": "approved",
        "signed_payload": "",
        "signature": "",
        "created_at": "2026-05-03T04:59:00Z",
    }
    fields.update(overrides)
    return ApprovalRecord(**fields)  # type: ignore[arg-type]


def test_require_approval_fails_closed_for_missing_unapproved_bad_signature_and_wrong_subject(tmp_path) -> None:
    registry = ControlPlaneRegistry(tmp_path / "control-plane.sqlite3")
    registry.initialize()

    with pytest.raises(ApprovalGateError, match="missing"):
        require_approval(
            registry,
            approval_id="missing",
            action="resume_run",
            subject="runs/run-123",
            key=KEY,
            now=NOW,
        )

    pending = sign_approval_record(_record(approval_id="pending", status="pending"), KEY)
    registry.create_approval(pending)
    with pytest.raises(ApprovalGateError, match="approved"):
        require_approval(registry, approval_id="pending", action="resume_run", subject="runs/run-123", key=KEY, now=NOW)

    signed = sign_approval_record(_record(approval_id="wrong-subject"), KEY)
    registry.create_approval(signed)
    with pytest.raises(ApprovalGateError, match="subject"):
        require_approval(registry, approval_id="wrong-subject", action="resume_run", subject="runs/other", key=KEY, now=NOW)

    bad_signature = sign_approval_record(_record(approval_id="bad-signature"), KEY)
    registry.create_approval(ApprovalRecord(**{**bad_signature.__dict__, "signature": "sha256=bad"}))
    with pytest.raises(ApprovalGateError, match="signature"):
        require_approval(registry, approval_id="bad-signature", action="resume_run", subject="runs/run-123", key=KEY, now=NOW)


def test_require_approval_uses_lifecycle_store_to_block_expired_revoked_and_replayed() -> None:
    store = ApprovalLifecycleStore(KEY, ttl=timedelta(seconds=5))
    requested = store.request(
        approval_id="appr-1",
        requested_by="ops",
        action="resume_run",
        subject="runs/run-123",
        created_at=NOW,
    )
    approved = store.approve(requested.approval_id, now=NOW + timedelta(seconds=1))

    executed = require_approval(
        store,
        approval_id=approved.approval_id,
        action="resume_run",
        subject="runs/run-123",
        key=KEY,
        now=NOW + timedelta(seconds=2),
    )
    assert executed.status == "EXECUTED"

    with pytest.raises(ApprovalGateError, match="replay|executable|stale|tampered"):
        require_approval(
            store,
            approval_id=approved.approval_id,
            action="resume_run",
            subject="runs/run-123",
            key=KEY,
            now=NOW + timedelta(seconds=3),
        )

    expired_store = ApprovalLifecycleStore(KEY, ttl=timedelta(seconds=5))
    expired = expired_store.approve(
        expired_store.request(
            approval_id="expired",
            requested_by="ops",
            action="resume_run",
            subject="runs/run-123",
            created_at=NOW,
        ).approval_id,
        now=NOW + timedelta(seconds=1),
    )
    with pytest.raises(ApprovalGateError, match="expired"):
        require_approval(expired_store, approval_id=expired.approval_id, action="resume_run", subject="runs/run-123", key=KEY, now=NOW + timedelta(seconds=6))


def test_e2e_simulated_runtime_action_requires_matching_approved_signed_approval(tmp_path) -> None:
    registry = ControlPlaneRegistry(tmp_path / "control-plane.sqlite3")
    registry.initialize()
    effects: list[str] = []

    def resume_run(*, approval_id: str | None, subject: str) -> str:
        require_approval(registry, approval_id=approval_id, action="resume_run", subject=subject, key=KEY, now=NOW)
        effects.append(subject)
        return "resumed"

    with pytest.raises(ApprovalGateError):
        resume_run(approval_id=None, subject="runs/run-123")

    pending = sign_approval_record(_record(approval_id="pending", status="pending"), KEY)
    registry.create_approval(pending)
    with pytest.raises(ApprovalGateError):
        resume_run(approval_id="pending", subject="runs/run-123")

    approved = sign_approval_record(_record(approval_id="approved"), KEY)
    registry.create_approval(approved)
    with pytest.raises(ApprovalGateError):
        resume_run(approval_id="approved", subject="runs/other")

    assert resume_run(approval_id="approved", subject="runs/run-123") == "resumed"
    assert effects == ["runs/run-123"]
