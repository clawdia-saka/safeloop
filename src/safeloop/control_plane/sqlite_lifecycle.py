"""Durable SQLite approval lifecycle store.

This store uses the existing control-plane ``approvals`` table for the current
signed lifecycle record and appends every transition to ``approval_events``.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import uuid

from safeloop.control_plane.lifecycle import (
    APPROVED,
    EXECUTED,
    EXPIRED,
    IN_FLIGHT,
    REJECTED,
    REQUESTED,
    REVOKED,
    ApprovalValidationError,
    TERMINAL_STATUSES,
    _format_time,
    _is_expired,
)
from safeloop.control_plane.registry import ApprovalEvent, ApprovalRecord, init_control_plane_registry
from safeloop.control_plane.signing import sign_approval_record, verify_approval_record


class SQLiteApprovalLifecycleStore:
    """SQLite-backed approval lifecycle with fail-closed HMAC validation."""

    def __init__(self, path: str | Path, key: bytes, *, ttl: timedelta = timedelta(minutes=10)) -> None:
        self.path = Path(path)
        self._key = key
        self._ttl = ttl
        init_control_plane_registry(self.path)

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
        signed = sign_approval_record(record, self._key)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO approvals(approval_id, requested_by, action, subject, status,
                                      signed_payload, signature, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _record_tuple(signed),
            )
            self._append_event(conn, signed, "REQUESTED", signed.requested_by, {"status": signed.status})
        return signed

    def approve(self, approval_id: str, *, now: datetime) -> ApprovalRecord:
        return self._transition(approval_id, APPROVED, now=now, allowed={REQUESTED}, event_type="APPROVED")

    def reject(self, approval_id: str, *, now: datetime) -> ApprovalRecord:
        return self._transition(approval_id, REJECTED, now=now, allowed={REQUESTED, APPROVED}, event_type="REJECTED")

    def revoke(self, approval_id: str, *, now: datetime) -> ApprovalRecord:
        return self._transition(approval_id, REVOKED, now=now, allowed={REQUESTED, APPROVED, IN_FLIGHT}, event_type="REVOKED")

    def get(self, approval_id: str) -> ApprovalRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT approval_id, requested_by, action, subject, status,
                       signed_payload, signature, created_at
                FROM approvals WHERE approval_id = ?
                """,
                (approval_id,),
            ).fetchone()
        return _record_from_row(row) if row else None

    def reserve_for_execution(
        self,
        presented: ApprovalRecord,
        *,
        requested_by: str,
        action: str,
        subject: str,
        now: datetime,
    ) -> ApprovalRecord:
        stored = self._validate_presented(presented, now=now)
        if stored.status != APPROVED:
            raise ApprovalValidationError(f"approval status is not executable: {stored.status}")
        self._assert_scope(stored, requested_by=requested_by, action=action, subject=subject)
        return self._store_transition(stored, IN_FLIGHT, actor=requested_by, event_type="IN_FLIGHT")

    def validate_in_flight_for_resume(
        self,
        presented: ApprovalRecord,
        *,
        action: str,
        subject: str,
        now: datetime,
    ) -> ApprovalRecord:
        stored = self._validate_presented(presented, now=now)
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
        stored = self._validate_presented(presented, now=now)
        if stored.status != IN_FLIGHT:
            raise ApprovalValidationError(f"approval status is not in-flight: {stored.status}")
        self._assert_scope(stored, requested_by=requested_by, action=action, subject=subject)
        return self._store_transition(stored, EXECUTED, actor=requested_by, event_type="EXECUTED")

    def execute_once(
        self,
        presented: ApprovalRecord,
        *,
        requested_by: str,
        action: str,
        subject: str,
        now: datetime,
    ) -> ApprovalRecord:
        stored = self._validate_presented(presented, now=now)
        if stored.status != APPROVED:
            raise ApprovalValidationError(f"approval status is not executable: {stored.status}")
        self._assert_scope(stored, requested_by=requested_by, action=action, subject=subject)
        return self._store_transition(stored, EXECUTED, actor=requested_by, event_type="EXECUTED")

    def list_events(self, approval_id: str | None = None) -> list[ApprovalEvent]:
        sql = "SELECT event_id, approval_id, event_type, actor, payload, created_at FROM approval_events"
        params: tuple[str, ...] = ()
        if approval_id is not None:
            sql += " WHERE approval_id = ?"
            params = (approval_id,)
        sql += " ORDER BY created_at ASC, event_id ASC"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [ApprovalEvent(row[0], row[1], row[2], row[3], row[4], row[5]) for row in rows]

    def _transition(self, approval_id: str, status: str, *, now: datetime, allowed: set[str], event_type: str) -> ApprovalRecord:
        stored = self.get(approval_id)
        if stored is None:
            raise ApprovalValidationError("unknown approval")
        self._assert_trusted(stored)
        if stored.status in TERMINAL_STATUSES:
            raise ApprovalValidationError(f"approval terminal: {stored.status}")
        if _is_expired(stored, now, self._ttl):
            expired = self._store_transition(stored, EXPIRED, actor=stored.requested_by, event_type="EXPIRED")
            raise ApprovalValidationError(f"approval terminal: {expired.status}")
        if stored.status not in allowed:
            raise ApprovalValidationError(f"cannot transition status={stored.status}")
        return self._store_transition(stored, status, actor=stored.requested_by, event_type=event_type)

    def _validate_presented(self, presented: ApprovalRecord, *, now: datetime) -> ApprovalRecord:
        stored = self.get(presented.approval_id)
        if stored is None:
            raise ApprovalValidationError("unknown approval")
        if stored != presented:
            raise ApprovalValidationError("approval is stale or tampered")
        self._assert_trusted(stored)
        if _is_expired(stored, now, self._ttl):
            self._store_transition(stored, EXPIRED, actor=stored.requested_by, event_type="EXPIRED")
            raise ApprovalValidationError("approval expired")
        return stored

    def _assert_trusted(self, record: ApprovalRecord) -> None:
        if not verify_approval_record(record, self._key):
            raise ApprovalValidationError("stored approval signature invalid")

    def _assert_scope(self, record: ApprovalRecord, *, requested_by: str, action: str, subject: str) -> None:
        if record.requested_by != requested_by:
            raise ApprovalValidationError("approval requester mismatch")
        if record.action != action:
            raise ApprovalValidationError("approval action mismatch")
        if record.subject != subject:
            raise ApprovalValidationError("approval subject mismatch")

    def _store_transition(self, record: ApprovalRecord, status: str, *, actor: str, event_type: str) -> ApprovalRecord:
        signed = sign_approval_record(replace(record, status=status), self._key)  # type: ignore[arg-type]
        with self._connect() as conn:
            current = _record_from_row(conn.execute(
                "SELECT approval_id, requested_by, action, subject, status, signed_payload, signature, created_at FROM approvals WHERE approval_id = ?",
                (record.approval_id,),
            ).fetchone())
            if current != record:
                raise ApprovalValidationError("approval is stale or tampered")
            conn.execute(
                """
                UPDATE approvals
                SET requested_by = ?, action = ?, subject = ?, status = ?, signed_payload = ?, signature = ?, created_at = ?
                WHERE approval_id = ?
                """,
                (signed.requested_by, signed.action, signed.subject, signed.status, signed.signed_payload, signed.signature, signed.created_at, signed.approval_id),
            )
            self._append_event(conn, signed, event_type, actor, {"status": signed.status})
        return signed

    def _append_event(self, conn: sqlite3.Connection, record: ApprovalRecord, event_type: str, actor: str, payload: dict[str, str]) -> None:
        conn.execute(
            """
            INSERT INTO approval_events(event_id, approval_id, event_type, actor, payload, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (str(uuid.uuid4()), record.approval_id, event_type, actor, json.dumps(payload, sort_keys=True), _format_time(datetime.now(timezone.utc))),
        )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn


def _record_tuple(record: ApprovalRecord) -> tuple[str, str, str, str, str, str, str, str]:
    return (record.approval_id, record.requested_by, record.action, record.subject, record.status, record.signed_payload, record.signature, record.created_at)


def _record_from_row(row: sqlite3.Row | tuple[str, ...]) -> ApprovalRecord:
    return ApprovalRecord(row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7])
