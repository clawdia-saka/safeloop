from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from safeloop.control_plane.lifecycle import (
    APPROVED,
    EXECUTED,
    EXPIRED,
    REJECTED,
    REQUESTED,
    REVOKED,
    ApprovalLifecycleStore,
    ApprovalValidationError,
)
from safeloop.control_plane.signing import verify_approval_record

KEY = b"approval-lifecycle-test-key"
T0 = datetime(2026, 5, 3, 4, 0, tzinfo=timezone.utc)


def _approved(store: ApprovalLifecycleStore):
    requested = store.request(
        approval_id="appr-1",
        requested_by="ops",
        action="resume_run",
        subject="runs/run-123",
        created_at=T0,
    )
    assert requested.status == REQUESTED
    return store.approve(requested.approval_id, now=T0 + timedelta(seconds=1))


def test_lifecycle_statuses_are_signed_and_execute_once_blocks_replay() -> None:
    store = ApprovalLifecycleStore(KEY)
    approved = _approved(store)

    assert approved.status == APPROVED
    assert verify_approval_record(approved, KEY)

    executed = store.execute_once(
        approved,
        requested_by="ops",
        action="resume_run",
        subject="runs/run-123",
        now=T0 + timedelta(seconds=2),
    )
    assert executed.status == EXECUTED
    assert verify_approval_record(executed, KEY)

    with pytest.raises(ApprovalValidationError, match="stale|tampered"):
        store.execute_once(
            approved,
            requested_by="ops",
            action="resume_run",
            subject="runs/run-123",
            now=T0 + timedelta(seconds=3),
        )


def test_expired_approval_fails_closed_and_records_expired_status() -> None:
    store = ApprovalLifecycleStore(KEY, ttl=timedelta(seconds=5))
    approved = _approved(store)

    with pytest.raises(ApprovalValidationError, match="expired"):
        store.execute_once(
            approved,
            requested_by="ops",
            action="resume_run",
            subject="runs/run-123",
            now=T0 + timedelta(seconds=6),
        )

    expired = store.get("appr-1")
    assert expired is not None
    assert expired.status == EXPIRED
    assert verify_approval_record(expired, KEY)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"requested_by": "mallory"}, "requester mismatch"),
        ({"action": "delete_run"}, "action mismatch"),
        ({"subject": "runs/other"}, "subject mismatch"),
    ],
)
def test_wrong_requester_action_or_subject_fails_closed(kwargs: dict[str, str], message: str) -> None:
    store = ApprovalLifecycleStore(KEY)
    approved = _approved(store)

    expected = {
        "requested_by": "ops",
        "action": "resume_run",
        "subject": "runs/run-123",
        **kwargs,
    }
    with pytest.raises(ApprovalValidationError, match=message):
        store.validate_for_execution(approved, now=T0 + timedelta(seconds=2), **expected)


def test_status_changes_invalidate_old_signature_for_reject_and_revoke() -> None:
    rejected_store = ApprovalLifecycleStore(KEY)
    rejected = rejected_store.reject(
        rejected_store.request(
            approval_id="reject-me",
            requested_by="ops",
            action="resume_run",
            subject="runs/run-123",
            created_at=T0,
        ).approval_id,
        now=T0 + timedelta(seconds=1),
    )
    assert rejected.status == REJECTED

    with pytest.raises(ApprovalValidationError, match="not executable"):
        rejected_store.execute_once(
            rejected,
            requested_by="ops",
            action="resume_run",
            subject="runs/run-123",
            now=T0 + timedelta(seconds=2),
        )

    revoked_store = ApprovalLifecycleStore(KEY)
    approved = _approved(revoked_store)
    revoked = revoked_store.revoke(approved.approval_id, now=T0 + timedelta(seconds=2))
    assert revoked.status == REVOKED
    assert verify_approval_record(revoked, KEY)

    with pytest.raises(ApprovalValidationError, match="stale|tampered"):
        revoked_store.execute_once(
            approved,
            requested_by="ops",
            action="resume_run",
            subject="runs/run-123",
            now=T0 + timedelta(seconds=3),
        )


def test_tampered_payload_and_fields_fail_closed() -> None:
    store = ApprovalLifecycleStore(KEY)
    approved = _approved(store)

    tampered_payload = replace(approved, signed_payload=approved.signed_payload.replace("resume_run", "delete_run"))
    with pytest.raises(ApprovalValidationError, match="stale|tampered"):
        store.execute_once(
            tampered_payload,
            requested_by="ops",
            action="resume_run",
            subject="runs/run-123",
            now=T0 + timedelta(seconds=2),
        )

    tampered_field = replace(approved, action="delete_run")
    with pytest.raises(ApprovalValidationError, match="stale|tampered"):
        store.execute_once(
            tampered_field,
            requested_by="ops",
            action="delete_run",
            subject="runs/run-123",
            now=T0 + timedelta(seconds=2),
        )
