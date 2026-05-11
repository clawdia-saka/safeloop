from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from safeloop.control_plane.lifecycle import APPROVED, EXECUTED, EXPIRED, IN_FLIGHT, ApprovalValidationError
from safeloop.control_plane.sqlite_lifecycle import SQLiteApprovalLifecycleStore

KEY = b"sqlite-lifecycle-test-key"
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def store(tmp_path, *, ttl=timedelta(minutes=10)) -> SQLiteApprovalLifecycleStore:
    return SQLiteApprovalLifecycleStore(tmp_path / "control.sqlite3", KEY, ttl=ttl)


def requested(s: SQLiteApprovalLifecycleStore, approval_id="ap-1"):
    return s.request(
        approval_id=approval_id,
        requested_by="alice",
        action="deploy",
        subject="service-a",
        created_at=NOW,
    )


def test_lifecycle_happy_path_and_events(tmp_path) -> None:
    s = store(tmp_path)
    rec = requested(s)
    approved = s.approve(rec.approval_id, now=NOW)
    assert approved.status == APPROVED
    in_flight = s.reserve_for_execution(approved, requested_by="alice", action="deploy", subject="service-a", now=NOW)
    assert in_flight.status == IN_FLIGHT
    executed = s.complete_execution(in_flight, requested_by="alice", action="deploy", subject="service-a", now=NOW)
    assert executed.status == EXECUTED
    assert s.get("ap-1") == executed
    assert [event.event_type for event in s.list_events("ap-1")] == ["REQUESTED", "APPROVED", "IN_FLIGHT", "EXECUTED"]


def test_execute_once_consumes_approval(tmp_path) -> None:
    s = store(tmp_path)
    approved = s.approve(requested(s).approval_id, now=NOW)
    executed = s.execute_once(approved, requested_by="alice", action="deploy", subject="service-a", now=NOW)
    assert executed.status == EXECUTED
    with pytest.raises(ApprovalValidationError):
        s.execute_once(approved, requested_by="alice", action="deploy", subject="service-a", now=NOW)


def test_replay_rejected_after_reservation(tmp_path) -> None:
    s = store(tmp_path)
    approved = s.approve(requested(s).approval_id, now=NOW)
    s.reserve_for_execution(approved, requested_by="alice", action="deploy", subject="service-a", now=NOW)
    with pytest.raises(ApprovalValidationError, match="stale|tampered"):
        s.reserve_for_execution(approved, requested_by="alice", action="deploy", subject="service-a", now=NOW)


def test_expired_rejected_and_marked_expired(tmp_path) -> None:
    s = store(tmp_path, ttl=timedelta(seconds=1))
    approved = s.approve(requested(s).approval_id, now=NOW)
    with pytest.raises(ApprovalValidationError, match="expired"):
        s.execute_once(approved, requested_by="alice", action="deploy", subject="service-a", now=NOW + timedelta(seconds=2))
    assert s.get("ap-1").status == EXPIRED  # type: ignore[union-attr]
    assert s.list_events("ap-1")[-1].event_type == "EXPIRED"


def test_stale_signed_record_rejected(tmp_path) -> None:
    s = store(tmp_path)
    approved = s.approve(requested(s).approval_id, now=NOW)
    s.revoke(approved.approval_id, now=NOW)
    with pytest.raises(ApprovalValidationError, match="stale|tampered"):
        s.execute_once(approved, requested_by="alice", action="deploy", subject="service-a", now=NOW)


def test_tampered_signed_payload_rejected(tmp_path) -> None:
    s = store(tmp_path)
    approved = s.approve(requested(s).approval_id, now=NOW)
    tampered = replace(approved, signed_payload=approved.signed_payload.replace("deploy", "delete"))
    with sqlite3.connect(tmp_path / "control.sqlite3") as conn:
        conn.execute("UPDATE approvals SET signed_payload = ? WHERE approval_id = ?", (tampered.signed_payload, approved.approval_id))
    with pytest.raises(ApprovalValidationError, match="signature invalid"):
        s.revoke(approved.approval_id, now=NOW)


def test_restart_preserves_approved_in_flight_executed(tmp_path) -> None:
    s1 = store(tmp_path)
    approved = s1.approve(requested(s1, "ap-approved").approval_id, now=NOW)
    in_flight = s1.reserve_for_execution(s1.approve(requested(s1, "ap-inflight").approval_id, now=NOW), requested_by="alice", action="deploy", subject="service-a", now=NOW)
    executed = s1.execute_once(s1.approve(requested(s1, "ap-executed").approval_id, now=NOW), requested_by="alice", action="deploy", subject="service-a", now=NOW)

    s2 = store(tmp_path)
    assert s2.get("ap-approved") == approved
    assert s2.get("ap-inflight") == in_flight
    assert s2.get("ap-executed") == executed
