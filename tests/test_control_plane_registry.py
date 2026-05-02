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
    assert registry.schema_version() == 1
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
            ("2", "schema_version"),
        )

    with pytest.raises(RuntimeError, match="unsupported future schema_version=2"):
        registry.initialize()

    assert registry.schema_version() == 2


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
