from __future__ import annotations

from datetime import datetime, timezone

from safeloop.control_plane.registry import ApprovalRecord, ControlPlaneRegistry
from safeloop.control_plane.sqlite_lifecycle import SQLiteApprovalLifecycleStore
from safeloop.journal import JournalReason, JournalState
from safeloop.runtime import ResumableExecution, Runtime
from safeloop.types import ActionEnvelope, EffectClass

KEY = b"sqlite-runtime-test-key"
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def action(key="key") -> ActionEnvelope:
    return ActionEnvelope(
        name="deploy",
        target="service-a",
        args={},
        diff="",
        actor="alice",
        privileges=[],
        idempotency_key=key,
        effect=EffectClass.REVERSIBLE_WRITE,
    )


def approve(store: SQLiteApprovalLifecycleStore, approval_id="ap-rt") -> ApprovalRecord:
    store.request(approval_id=approval_id, requested_by="alice", action="deploy", subject="service-a", created_at=NOW)
    return store.approve(approval_id, now=NOW)


def test_runtime_can_start_execution_with_sqlite_lifecycle_after_restart(tmp_path) -> None:
    db = tmp_path / "control.sqlite3"
    journal = tmp_path / "journal.jsonl"
    approved = approve(SQLiteApprovalLifecycleStore(db, KEY))

    restarted_store = SQLiteApprovalLifecycleStore(db, KEY)
    executions: list[object | None] = []
    result = Runtime(journal).run(
        run_id="run-1",
        action=action("a1"),
        executor=lambda checkpoint: executions.append(checkpoint),
        control_plane_approvals=restarted_store,
        approval_id=approved.approval_id,
        approval_key=KEY,
        approval_now=NOW,
    )

    assert result.state == JournalState.APPLIED
    assert executions == [None]
    assert restarted_store.get(approved.approval_id).status == "EXECUTED"  # type: ignore[union-attr]


def test_runtime_resume_validation_after_store_restart(tmp_path) -> None:
    db = tmp_path / "control.sqlite3"
    journal = tmp_path / "journal.jsonl"
    approved = approve(SQLiteApprovalLifecycleStore(db, KEY), "ap-resume")
    runtime = Runtime(journal)
    act = action("resume-key")

    first = runtime.run(
        run_id="run-resume",
        action=act,
        executor=lambda checkpoint: (_ for _ in ()).throw(ResumableExecution({"step": 1})),
        control_plane_approvals=SQLiteApprovalLifecycleStore(db, KEY),
        approval_id=approved.approval_id,
        approval_key=KEY,
        approval_now=NOW,
    )
    assert first.state == JournalState.RESUMABLE
    assert SQLiteApprovalLifecycleStore(db, KEY).get(approved.approval_id).status == "IN_FLIGHT"  # type: ignore[union-attr]

    executions: list[object | None] = []
    resumed = runtime.run(
        run_id="run-resume",
        action=act,
        executor=lambda checkpoint: executions.append(checkpoint),
        control_plane_approvals=SQLiteApprovalLifecycleStore(db, KEY),
        approval_id=approved.approval_id,
        approval_key=KEY,
        approval_now=NOW,
    )
    assert resumed.state == JournalState.APPLIED
    assert executions == [{"step": 1}]
    assert SQLiteApprovalLifecycleStore(db, KEY).get(approved.approval_id).status == "EXECUTED"  # type: ignore[union-attr]


def test_runtime_rejects_lookup_only_registry_for_enforcement(tmp_path) -> None:
    registry = ControlPlaneRegistry(tmp_path / "control.sqlite3")
    registry.initialize()
    registry.create_approval(ApprovalRecord("ap", "alice", "deploy", "service-a", "approved", "", "", NOW.isoformat()))

    result = Runtime(tmp_path / "journal.jsonl").run(
        run_id="run-blocked",
        action=action("blocked-key"),
        executor=lambda checkpoint: None,
        control_plane_approvals=registry,
        approval_id="ap",
        approval_key=KEY,
        approval_now=NOW,
    )
    assert result.state == JournalState.FAILED
    assert result.reason == JournalReason.APPROVAL_BLOCK
    assert "lifecycle approval store" in (result.error or "")
