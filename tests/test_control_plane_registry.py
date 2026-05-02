from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from safeloop.control_plane.registry import (
    ApprovalRecord,
    ControlPlaneRegistry,
    RegistryUser,
)


def test_registry_initializes_sqlite_file_with_expected_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "control-plane.sqlite3"

    registry = ControlPlaneRegistry(db_path)
    registry.initialize()

    assert db_path.exists()
    assert registry.schema_version() == 1
    assert registry.table_names() == {
        "approval_requests",
        "control_plane_metadata",
        "external_anchors",
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
