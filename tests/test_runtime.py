from __future__ import annotations

import pytest

from safeloop.hooks import (
    ApprovalDecision,
    ApprovalHookRegistry,
    CompensationHookRegistry,
)
from safeloop.journal import JournalState, validate_transition
from safeloop.runtime import ResumableExecution, Runtime
from safeloop.types import ActionEnvelope, EffectClass


def make_action(effect: EffectClass, key: str | None = None) -> ActionEnvelope:
    return ActionEnvelope(
        name="demo",
        target="system",
        args={},
        diff="",
        actor="tester",
        privileges=[],
        idempotency_key=key or f"key-{effect.value}",
        effect=effect,
    )


def make_runtime(tmp_path) -> Runtime:
    return Runtime(tmp_path / "journal.jsonl")


def test_read_only_action_executes_without_approval(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    action = make_action(EffectClass.READ_ONLY)
    approvals = ApprovalHookRegistry()
    seen: list[ActionEnvelope] = []
    approvals.register(lambda envelope: seen.append(envelope) or ApprovalDecision.BLOCK)
    executions: list[object | None] = []

    result = runtime.run(
        run_id="run-read-only",
        action=action,
        executor=lambda checkpoint: executions.append(checkpoint) or {"ok": True},
        approval_hooks=approvals,
    )

    assert result.state == JournalState.APPLIED
    assert seen == []
    assert executions == [None]


def test_compensatable_action_can_fail_and_call_compensation(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    action = make_action(EffectClass.COMPENSATABLE_WRITE)
    compensations: list[str] = []
    hooks = CompensationHookRegistry()
    hooks.register(lambda envelope, error: compensations.append(str(error)))

    result = runtime.run(
        run_id="run-compensate",
        action=action,
        executor=lambda checkpoint: (_ for _ in ()).throw(RuntimeError("boom")),
        compensation_hooks=hooks,
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


def test_escalated_action_hands_off_before_execution(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    action = make_action(EffectClass.IRREVERSIBLE_WRITE)
    approvals = ApprovalHookRegistry()
    approvals.register(lambda envelope: ApprovalDecision.ESCALATE)
    executed: list[object | None] = []

    result = runtime.run(
        run_id="run-handoff",
        action=action,
        executor=lambda checkpoint: executed.append(checkpoint),
        approval_hooks=approvals,
    )

    assert result.state == JournalState.HANDED_OFF
    assert executed == []
    assert [entry.state for entry in runtime.history("run-handoff")] == [
        JournalState.PROPOSED,
        JournalState.APPROVED,
        JournalState.HANDED_OFF,
    ]


def test_blocked_action_fails_before_execution(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    action = make_action(EffectClass.REVERSIBLE_WRITE)
    approvals = ApprovalHookRegistry()
    approvals.register(lambda envelope: ApprovalDecision.BLOCK)
    executed: list[object | None] = []

    result = runtime.run(
        run_id="run-blocked",
        action=action,
        executor=lambda checkpoint: executed.append(checkpoint),
        approval_hooks=approvals,
    )

    assert result.state == JournalState.FAILED
    assert executed == []
    assert [entry.state for entry in runtime.history("run-blocked")] == [
        JournalState.PROPOSED,
        JournalState.FAILED,
    ]


def test_existing_run_rejects_different_action_identity(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
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


def test_approval_hook_failure_marks_run_failed(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    action = make_action(EffectClass.REVERSIBLE_WRITE, key="approval-failure")
    approvals = ApprovalHookRegistry()
    approvals.register(lambda envelope: (_ for _ in ()).throw(RuntimeError("approval failed")))

    result = runtime.run(
        run_id="run-approval-failure",
        action=action,
        executor=lambda checkpoint: {"ok": True},
        approval_hooks=approvals,
    )

    assert result.state == JournalState.FAILED
    assert [entry.state for entry in runtime.history("run-approval-failure")] == [
        JournalState.PROPOSED,
        JournalState.FAILED,
    ]


def test_compensation_hook_failure_marks_run_failed(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    action = make_action(EffectClass.COMPENSATABLE_WRITE, key="comp-failure")
    hooks = CompensationHookRegistry()
    hooks.register(lambda envelope, error: (_ for _ in ()).throw(RuntimeError("comp failed")))

    result = runtime.run(
        run_id="run-compensation-failure",
        action=action,
        executor=lambda checkpoint: (_ for _ in ()).throw(RuntimeError("boom")),
        compensation_hooks=hooks,
    )

    assert result.state == JournalState.FAILED
    assert [entry.state for entry in runtime.history("run-compensation-failure")] == [
        JournalState.PROPOSED,
        JournalState.APPROVED,
        JournalState.EXECUTING,
        JournalState.COMPENSATING,
        JournalState.FAILED,
    ]


def test_compensating_can_fail_terminally() -> None:
    validate_transition(JournalState.COMPENSATING, JournalState.FAILED)


def test_resumable_state_can_be_reentered_and_continued(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
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


def test_repeated_resumable_execution_replaces_checkpoint_until_terminal(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    action = make_action(EffectClass.REVERSIBLE_WRITE, key="repeat-resume")
    checkpoints: list[object | None] = []

    def executor(checkpoint: object | None) -> dict[str, bool]:
        checkpoints.append(checkpoint)
        if checkpoint is None:
            raise ResumableExecution(checkpoint={"step": 1})
        if checkpoint == {"step": 1}:
            raise ResumableExecution(checkpoint={"step": 2})
        return {"resumed": True}

    first_result = runtime.run(run_id="run-repeat-resume", action=action, executor=executor)
    second_result = runtime.run(run_id="run-repeat-resume", action=action, executor=executor)
    third_result = runtime.run(run_id="run-repeat-resume", action=action, executor=executor)

    assert [first_result.state, second_result.state, third_result.state] == [
        JournalState.RESUMABLE,
        JournalState.RESUMABLE,
        JournalState.APPLIED,
    ]
    assert checkpoints == [None, {"step": 1}, {"step": 2}]
    assert runtime.checkpoint_for("run-repeat-resume") is None
    assert [entry.state for entry in runtime.history("run-repeat-resume")] == [
        JournalState.PROPOSED,
        JournalState.APPROVED,
        JournalState.EXECUTING,
        JournalState.RESUMABLE,
        JournalState.EXECUTING,
        JournalState.RESUMABLE,
        JournalState.EXECUTING,
        JournalState.APPLIED,
    ]


def test_terminal_runs_clear_checkpoint_and_do_not_reexecute(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    action = make_action(EffectClass.REVERSIBLE_WRITE, key="terminal-resume")
    checkpoints: list[object | None] = []

    def executor(checkpoint: object | None) -> dict[str, bool]:
        checkpoints.append(checkpoint)
        if checkpoint is None:
            raise ResumableExecution(checkpoint={"step": 1})
        return {"done": True}

    runtime.run(run_id="run-terminal-resume", action=action, executor=executor)
    result = runtime.run(run_id="run-terminal-resume", action=action, executor=executor)
    rerun = runtime.run(run_id="run-terminal-resume", action=action, executor=executor)

    assert result.state == JournalState.APPLIED
    assert rerun.state == JournalState.APPLIED
    assert checkpoints == [None, {"step": 1}]
    assert runtime.checkpoint_for("run-terminal-resume") is None
    assert [entry.state for entry in runtime.history("run-terminal-resume")] == [
        JournalState.PROPOSED,
        JournalState.APPROVED,
        JournalState.EXECUTING,
        JournalState.RESUMABLE,
        JournalState.EXECUTING,
        JournalState.APPLIED,
    ]
