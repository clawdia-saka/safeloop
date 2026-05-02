"""Runtime approval gate for SafeLoop control-plane approvals.

The gate is intentionally small and fail-closed: runtime actions must present an
approval id that resolves to a signed, approved record for the exact action and
subject.  When given the lifecycle store introduced in 0.1.0, the gate delegates
execution validation to it so expiry, revocation, and replay protections are
preserved.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

from safeloop.control_plane.lifecycle import ApprovalValidationError
from safeloop.control_plane.registry import ApprovalRecord
from safeloop.control_plane.signing import verify_approval_record

_APPROVED_STATUSES = frozenset({"approved", "APPROVED"})


class ApprovalGateError(PermissionError):
    """Raised when a runtime action lacks a valid approval."""


class _ApprovalLookup(Protocol):
    def get_approval(self, approval_id: str) -> ApprovalRecord | None: ...


class _LifecycleStore(Protocol):
    def get(self, approval_id: str) -> ApprovalRecord | None: ...

    def execute_once(
        self,
        presented: ApprovalRecord,
        *,
        requested_by: str,
        action: str,
        subject: str,
        now: datetime,
    ) -> ApprovalRecord: ...


def require_approval(
    approvals: _ApprovalLookup | _LifecycleStore,
    *,
    approval_id: str | None,
    action: str,
    subject: str,
    key: bytes,
    now: datetime | None = None,
) -> ApprovalRecord:
    """Return the trusted approval for a runtime action or raise fail-closed.

    ``approvals`` may be the SQLite control-plane registry (``get_approval``) or
    the lifecycle store (``get`` + ``execute_once``).  Lifecycle stores consume
    the approval via ``execute_once`` to prevent replay.
    """

    if not approval_id:
        raise ApprovalGateError("approval_id missing")
    checked_at = _utc_now() if now is None else now

    if hasattr(approvals, "get") and hasattr(approvals, "execute_once"):
        record = approvals.get(approval_id)  # type: ignore[attr-defined]
        if record is None:
            raise ApprovalGateError("approval_id missing or unknown")
        try:
            return approvals.execute_once(  # type: ignore[attr-defined]
                record,
                requested_by=record.requested_by,
                action=action,
                subject=subject,
                now=checked_at,
            )
        except ApprovalValidationError as exc:
            raise ApprovalGateError(str(exc)) from exc

    if not hasattr(approvals, "get_approval"):
        raise ApprovalGateError("approval store does not support approval lookup")

    record = approvals.get_approval(approval_id)  # type: ignore[attr-defined]
    if record is None:
        raise ApprovalGateError("approval_id missing or unknown")
    if record.status not in _APPROVED_STATUSES:
        raise ApprovalGateError("approval status is not approved")
    if not verify_approval_record(record, key):
        raise ApprovalGateError("approval signature invalid")
    if record.action != action:
        raise ApprovalGateError("approval action mismatch")
    if record.subject != subject:
        raise ApprovalGateError("approval subject mismatch")
    return record


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
