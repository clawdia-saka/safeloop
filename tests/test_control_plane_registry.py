from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from safeloop.control_plane.registry import (
    ApprovalEvent,
    ApprovalRecord,
    ControlPlaneRegistry,
    OperatorTokenRecord,
    RegistryUser,
)


def test_registry_initializes_sqlite_file_with_expected_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "control-plane.sqlite3"

    registry = ControlPlaneRegistry(db_path)
    registry.initialize()

    assert db_path.exists()
    assert registry.schema_version() == 4
    assert registry.table_names() == {
        "approval_events",
        "approvals",
        "control_plane_metadata",
        "external_anchors",
        "local_tokens",
        "users",
    }


def test_registry_initialization_does_not_downgrade_newer_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "control-plane.sqlite3"
    registry = ControlPlaneRegistry(db_path)
    registry.initialize()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE control_plane_metadata SET value = ? WHERE key = ?",
            ("5", "schema_version"),
        )

    with pytest.raises(RuntimeError, match="unsupported future schema_version=5"):
        registry.initialize()

    assert registry.schema_version() == 5


def test_registry_migrates_v1_approval_requests_to_v2_approvals(tmp_path: Path) -> None:
    db_path = tmp_path / "control-plane.sqlite3"
    old = ApprovalRecord(
        approval_id="appr-old",
        requested_by="ops",
        action="resume_run",
        subject="run-old",
        status="pending",
        signed_payload="{}",
        signature="sha256=old",
        created_at="2026-05-03T03:00:00Z",
    )
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE control_plane_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO control_plane_metadata(key, value) VALUES ('schema_version', '1');
            CREATE TABLE approval_requests (
                approval_id TEXT PRIMARY KEY,
                requested_by TEXT NOT NULL,
                action TEXT NOT NULL,
                subject TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected', 'expired')),
                signed_payload TEXT NOT NULL,
                signature TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO approval_requests(
                approval_id, requested_by, action, subject, status,
                signed_payload, signature, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                old.approval_id,
                old.requested_by,
                old.action,
                old.subject,
                old.status,
                old.signed_payload,
                old.signature,
                old.created_at,
            ),
        )

    registry = ControlPlaneRegistry(db_path)
    registry.initialize()

    assert registry.schema_version() == 4
    assert registry.get_approval("appr-old") == old
    assert "approval_requests" in registry.table_names()
    assert "approvals" in registry.table_names()


def test_registry_migrates_v2_approvals_status_constraint_for_lifecycle(tmp_path: Path) -> None:
    db_path = tmp_path / "control-plane.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE control_plane_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO control_plane_metadata(key, value) VALUES ('schema_version', '2');
            CREATE TABLE approvals (
                approval_id TEXT PRIMARY KEY,
                requested_by TEXT NOT NULL,
                action TEXT NOT NULL,
                subject TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected', 'expired')),
                signed_payload TEXT NOT NULL,
                signature TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )

    registry = ControlPlaneRegistry(db_path)
    registry.initialize()
    lifecycle_approval = ApprovalRecord(
        approval_id="appr-life",
        requested_by="ops",
        action="resume_run",
        subject="run-life",
        status="REQUESTED",
        signed_payload="{}",
        signature="sha256=life",
        created_at="2026-05-03T03:00:00Z",
    )
    registry.create_approval(lifecycle_approval)

    assert registry.schema_version() == 4
    assert registry.get_approval("appr-life") == lifecycle_approval


def test_registry_upserts_and_lists_users_by_stable_role_order(tmp_path: Path) -> None:
    registry = ControlPlaneRegistry(tmp_path / "control-plane.sqlite3")
    registry.initialize()

    registry.upsert_user(RegistryUser(user_id="ada", role="admin", display_name="Ada"))
    registry.upsert_user(RegistryUser(user_id="val", role="viewer", display_name="Val"))
    registry.upsert_user(RegistryUser(user_id="ops", role="operator", display_name="Ops"))
    registry.upsert_user(RegistryUser(user_id="ada", role="operator", display_name="Ada L."))

    assert registry.list_users() == [
        RegistryUser(user_id="ada", role="operator", display_name="Ada L."),
        RegistryUser(user_id="ops", role="operator", display_name="Ops"),
        RegistryUser(user_id="val", role="viewer", display_name="Val"),
    ]


def test_registry_persists_approval_records_without_hmac_validation(tmp_path: Path) -> None:
    registry = ControlPlaneRegistry(tmp_path / "control-plane.sqlite3")
    registry.initialize()

    approval = ApprovalRecord(
        approval_id="appr-1",
        requested_by="ops",
        action="resume_run",
        subject="run-123",
        status="pending",
        signed_payload="{\"action\":\"resume_run\"}",
        signature="sha256=test-signature-placeholder",
        created_at="2026-05-03T03:00:00Z",
    )
    registry.record_approval(approval)

    assert registry.get_approval("appr-1") == approval
    assert registry.list_approvals(status="pending") == [approval]


def test_registry_appends_approval_events_as_audit_log(tmp_path: Path) -> None:
    registry = ControlPlaneRegistry(tmp_path / "control-plane.sqlite3")
    registry.initialize()
    approval = ApprovalRecord(
        approval_id="appr-1",
        requested_by="ops",
        action="resume_run",
        subject="run-123",
        status="pending",
        signed_payload="{}",
        signature="sha256=test",
        created_at="2026-05-03T03:00:00Z",
    )
    registry.create_approval(approval)

    created = ApprovalEvent(
        event_id="evt-1",
        approval_id="appr-1",
        event_type="created",
        actor="ops",
        payload='{"status":"pending"}',
        created_at="2026-05-03T03:00:01Z",
    )
    commented = ApprovalEvent(
        event_id="evt-2",
        approval_id="appr-1",
        event_type="commented",
        actor="ada",
        payload='{"note":"reviewed"}',
        created_at="2026-05-03T03:00:02Z",
    )

    registry.append_approval_event(commented)
    registry.append_approval_event(created)

    assert registry.list_approval_events("appr-1") == [created, commented]
    assert registry.list_approval_events() == [created, commented]


def test_registry_operator_token_table_matches_local_token_store_shape(tmp_path: Path) -> None:
    registry = ControlPlaneRegistry(tmp_path / "control-plane.sqlite3")
    registry.initialize()
    record = OperatorTokenRecord(
        token_hash="abc123",
        salt="salt",
        user_id="ops",
        role="operator",
    )

    registry.upsert_operator_token(record)

    assert registry.list_operator_tokens() == [record]
