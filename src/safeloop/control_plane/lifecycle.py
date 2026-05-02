"""Approval lifecycle validation primitives for the SafeLoop control plane.

This module is intentionally small and storage-agnostic.  It validates an
approval against the current stored record instead of trusting client-supplied
payloads, so replayed or stale signatures fail closed after any status change.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from safeloop.control_plane.registry import ApprovalRecord
from safeloop.control_plane.signing import sign_approval_record, verify_approval_record

REQUESTED = "REQUESTED"
APPROVED = "APPROVED"
IN_FLIGHT = "IN_FLIGHT"
REJECTED = "REJECTED"
EXECUTED = "EXECUTED"
EXPIRED = "EXPIRED"
REVOKED = "REVOKED"

TERMINAL_STATUSES = frozenset({REJECTED, EXECUTED, EXPIRED, REVOKED})
EXECUTABLE_STATUSES = frozenset({APPROVED})


class ApprovalValidationError(PermissionError):
    """Raised when an approval cannot be trusted or used."""


class ApprovalLifecycleStore:
    """Minimal in-memory lifecycle store with HMAC-signed status transitions."""

    def __init__(self, key: bytes, *, ttl: timedelta = timedelta(minutes=10)) -> None:
        self._key = key
        self._ttl = ttl
        self._records: dict[str, ApprovalRecord] = {}

    def request(
        self,
        *,
        approval_id: str,
        requested_by: str,
        action: str,
        subject: str,
        created_at: datetime,
    ) -> ApprovalRecord:
        record = ApprovalRecord(
            approval_id=approval_id,
            requested_by=requested_by,
            action=action,
            subject=subject,
            status=REQUESTED,  # type: ignore[arg-type]
            signed_payload="",
            signature="",
            created_at=_format_time(created_at),
        )
        return self._store_signed(record)

    def approve(self, approval_id: str, *, now: datetime) -> ApprovalRecord:
        record = self._current_nonterminal(approval_id, now=now)
        if record.status != REQUESTED:
            raise ApprovalValidationError(f"cannot approve status={record.status}")
        return self._store_signed(replace(record, status=APPROVED))  # type: ignore[arg-type]

    def reject(self, approval_id: str, *, now: datetime) -> ApprovalRecord:
        record = self._current_nonterminal(approval_id, now=now)
        return self._store_signed(replace(record, status=REJECTED))  # type: ignore[arg-type]

    def revoke(self, approval_id: str, *, now: datetime) -> ApprovalRecord:
        record = self._current_nonterminal(approval_id, now=now)
        return self._store_signed(replace(record, status=REVOKED))  # type: ignore[arg-type]

    def validate_for_execution(
        self,
        presented: ApprovalRecord,
        *,
        requested_by: str,
        action: str,
        subject: str,
        now: datetime,
    ) -> ApprovalRecord:
        stored = self._records.get(presented.approval_id)
        if stored is None:
            raise ApprovalValidationError("unknown approval")
        if stored != presented:
            raise ApprovalValidationError("approval is stale or tampered")
        if not verify_approval_record(stored, self._key):
            raise ApprovalValidationError("invalid approval signature")
        if _is_expired(stored, now, self._ttl):
            self._store_signed(replace(stored, status=EXPIRED))  # type: ignore[arg-type]
            raise ApprovalValidationError("approval expired")
        if stored.status not in EXECUTABLE_STATUSES:
            raise ApprovalValidationError(f"approval status is not executable: {stored.status}")
        if stored.requested_by != requested_by:
            raise ApprovalValidationError("approval requester mismatch")
        if stored.action != action:
            raise ApprovalValidationError("approval action mismatch")
        if stored.subject != subject:
            raise ApprovalValidationError("approval subject mismatch")
        return stored

    def reserve_for_execution(
        self,
        presented: ApprovalRecord,
        *,
        requested_by: str,
        action: str,
        subject: str,
        now: datetime,
    ) -> ApprovalRecord:
        approved = self.validate_for_execution(
            presented,
            requested_by=requested_by,
            action=action,
            subject=subject,
            now=now,
        )
        return self._store_signed(replace(approved, status=IN_FLIGHT))  # type: ignore[arg-type]

    def validate_in_flight_for_resume(
        self,
        presented: ApprovalRecord,
        *,
        action: str,
        subject: str,
        now: datetime,
    ) -> ApprovalRecord:
        stored = self._records.get(presented.approval_id)
        if stored is None:
            raise ApprovalValidationError("unknown approval")
        if stored != presented:
            raise ApprovalValidationError("approval is stale or tampered")
        if not verify_approval_record(stored, self._key):
            raise ApprovalValidationError("invalid approval signature")
        if _is_expired(stored, now, self._ttl):
            self._store_signed(replace(stored, status=EXPIRED))  # type: ignore[arg-type]
            raise ApprovalValidationError("approval expired")
        if stored.status != IN_FLIGHT:
            raise ApprovalValidationError(f"approval status is not in-flight: {stored.status}")
        if stored.action != action:
            raise ApprovalValidationError("approval action mismatch")
        if stored.subject != subject:
            raise ApprovalValidationError("approval subject mismatch")
        return stored

    def complete_execution(
        self,
        presented: ApprovalRecord,
        *,
        requested_by: str,
        action: str,
        subject: str,
        now: datetime,
    ) -> ApprovalRecord:
        stored = self._records.get(presented.approval_id)
        if stored is None:
            raise ApprovalValidationError("unknown approval")
        if stored != presented:
            raise ApprovalValidationError("approval is stale or tampered")
        if not verify_approval_record(stored, self._key):
            raise ApprovalValidationError("invalid approval signature")
        if _is_expired(stored, now, self._ttl):
            self._store_signed(replace(stored, status=EXPIRED))  # type: ignore[arg-type]
            raise ApprovalValidationError("approval expired")
        if stored.status != IN_FLIGHT:
            raise ApprovalValidationError(f"approval status is not in-flight: {stored.status}")
        if stored.requested_by != requested_by:
            raise ApprovalValidationError("approval requester mismatch")
        if stored.action != action:
            raise ApprovalValidationError("approval action mismatch")
        if stored.subject != subject:
            raise ApprovalValidationError("approval subject mismatch")
        return self._store_signed(replace(stored, status=EXECUTED))  # type: ignore[arg-type]

    def execute_once(
        self,
        presented: ApprovalRecord,
        *,
        requested_by: str,
        action: str,
        subject: str,
        now: datetime,
    ) -> ApprovalRecord:
        approved = self.validate_for_execution(
            presented,
            requested_by=requested_by,
            action=action,
            subject=subject,
            now=now,
        )
        return self._store_signed(replace(approved, status=EXECUTED))  # type: ignore[arg-type]

    def get(self, approval_id: str) -> ApprovalRecord | None:
        return self._records.get(approval_id)

    def _current_nonterminal(self, approval_id: str, *, now: datetime) -> ApprovalRecord:
        record = self._records.get(approval_id)
        if record is None:
            raise ApprovalValidationError("unknown approval")
        if not verify_approval_record(record, self._key):
            raise ApprovalValidationError("stored approval signature invalid")
        if record.status in TERMINAL_STATUSES:
            raise ApprovalValidationError(f"approval terminal: {record.status}")
        if _is_expired(record, now, self._ttl):
            expired = self._store_signed(replace(record, status=EXPIRED))  # type: ignore[arg-type]
            raise ApprovalValidationError(f"approval terminal: {expired.status}")
        return record

    def _store_signed(self, record: ApprovalRecord) -> ApprovalRecord:
        signed = sign_approval_record(record, self._key)
        self._records[signed.approval_id] = signed
        return signed


def _format_time(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _is_expired(record: ApprovalRecord, now: datetime, ttl: timedelta) -> bool:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc) > _parse_time(record.created_at) + ttl
