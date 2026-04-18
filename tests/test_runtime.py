from __future__ import annotations

import pytest

from safeloop.journal import JournalState
from safeloop.runtime import ResumableExecution, Runtime
from safeloop.types import ActionEnvelope, EffectClass


def make_action(effect: EffectClass) -> ActionEnvelope:
    return ActionEnvelope(
        name="demo",
        target="system",
        args={},
        diff="",
        actor="tester",
        privileges=[],
        idempotency_key=f"key-{effect.value}",
        effect=effect,
    )


def test_read_only_action_executes_without_approval() -> None:
    runtime = Runtime()
    action = make_action(EffectClass.READ_ONLY)
    approvals: list[ActionEnvelope] = []
    executions: list[object | None] = []

    result = runtime.run(
        run_id="run-read-only",
        action=action,
        executor=lambda checkpoint: executions.append(checkpoint) or {"ok": True},
        approval_hook=lambda envelope: approvals.append(envelope) or "approved",
    )

    assert result.state == JournalState.APPLIED
    assert approvals == []
    assert executions == [None]
    assert [entry.state for entry in runtime.history("run-read-only")] == [
        JournalState.PROPOSED,
        JournalState.APPROVED,
        JournalState.EXECUTING,
        JournalState.APPLIED,
    ]


def test_compensatable_action_can_fail_and_call_compensation() -> None:
    runtime = Runtime()
    action = make_action(EffectClass.COMPENSATABLE_WRITE)
    compensations: list[str] = []

    result = runtime.run(
        run_id="run-compensate",
        action=action,
        executor=lambda checkpoint: (_ for _ in ()).throw(RuntimeError("boom")),
        compensation_hook=lambda envelope, error: compensations.append(str(error)),
    )

    assert result.state == JournalState.COMPENSATED
    assert compensations == ["boom"]
    assert [entry.state for entry in runtime.history("run-compensate")] == [
        JournalState.PROPOSED,
        JournalState.APPROVED,
        JournalState.EXECUTING,
        JournalState.COMPENSATING,
        JournalState.COMPENSATED,
    ]


def test_irreversible_action_escalates_when_approval_demands_it() -> None:
    runtime = Runtime()
    action = make_action(EffectClass.IRREVERSIBLE_WRITE)
    executed: list[object | None] = []
    approvals: list[ActionEnvelope] = []

    result = runtime.run(
        run_id="run-handoff",
        action=action,
        executor=lambda checkpoint: executed.append(checkpoint),
        approval_hook=lambda envelope: approvals.append(envelope) or "handoff",
    )

    assert result.state == JournalState.HANDED_OFF
    assert approvals == [action]
    assert executed == []
    assert [entry.state for entry in runtime.history("run-handoff")] == [
        JournalState.PROPOSED,
        JournalState.APPROVED,
        JournalState.HANDED_OFF,
    ]


def test_handoff_path_uses_validated_journal_transition() -> None:
    runtime = Runtime()
    action = make_action(EffectClass.IRREVERSIBLE_WRITE)

    result = runtime.run(
        run_id="run-handoff-validated",
        action=action,
        executor=lambda checkpoint: {"ok": True},
        approval_hook=lambda envelope: "handoff",
    )

    assert result.state == JournalState.HANDED_OFF
    assert runtime.history("run-handoff-validated")[-1].state == JournalState.HANDED_OFF
    assert runtime.checkpoint_for("run-handoff-validated") is None


def test_existing_run_rejects_different_action_identity() -> None:
    runtime = Runtime()
    original_action = make_action(EffectClass.REVERSIBLE_WRITE)
    different_action = original_action.model_copy(update={"idempotency_key": "different-key"})

    runtime.run(
        run_id="run-identity",
        action=original_action,
        executor=lambda checkpoint: {"ok": True},
    )

    with pytest.raises(ValueError, match="already bound to action"):
        runtime.run(
            run_id="run-identity",
            action=different_action,
            executor=lambda checkpoint: {"ok": True},
        )


def test_resumable_state_can_be_reentered_and_continued() -> None:
    runtime = Runtime()
    action = make_action(EffectClass.REVERSIBLE_WRITE)
    checkpoints: list[object | None] = []

    def executor(checkpoint: object | None) -> dict[str, bool]:
        checkpoints.append(checkpoint)
        if checkpoint is None:
            raise ResumableExecution(checkpoint={"step": 1})
        return {"resumed": True}

    first_result = runtime.run(
        run_id="run-resume",
        action=action,
        executor=executor,
    )
    second_result = runtime.run(
        run_id="run-resume",
        action=action,
        executor=executor,
    )

    assert first_result.state == JournalState.RESUMABLE
    assert second_result.state == JournalState.APPLIED
    assert checkpoints == [None, {"step": 1}]
    assert runtime.checkpoint_for("run-resume") is None
    assert [entry.state for entry in runtime.history("run-resume")] == [
        JournalState.PROPOSED,
        JournalState.APPROVED,
        JournalState.EXECUTING,
        JournalState.RESUMABLE,
        JournalState.EXECUTING,
        JournalState.APPLIED,
    ]


def test_checkpoint_is_cleared_after_initial_apply() -> None:
    runtime = Runtime()
    action = make_action(EffectClass.REVERSIBLE_WRITE)

    runtime.run(
        run_id="run-apply",
        action=action,
        executor=lambda checkpoint: {"ok": checkpoint is None},
    )

    assert runtime.checkpoint_for("run-apply") is None
