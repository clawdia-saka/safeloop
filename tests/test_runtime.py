from __future__ import annotations

from datetime import datetime, timezone

import pytest

from safeloop.control_plane.lifecycle import ApprovalLifecycleStore, ApprovalValidationError, EXECUTED, IN_FLIGHT
from safeloop.control_plane.registry import ApprovalRecord, ControlPlaneRegistry
from safeloop.control_plane.signing import sign_approval_record
from safeloop.hooks import (
    ApprovalDecision,
    ApprovalHookRegistry,
    CompensationHookRegistry,
)
from safeloop.journal import JournalReason, JournalState, validate_transition
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


def test_control_plane_gate_blocks_write_without_approval_before_execution(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    lifecycle = ApprovalLifecycleStore(b"runtime-gate-test-key")
    action = make_action(EffectClass.REVERSIBLE_WRITE, key="runtime-gated-missing")
    executions: list[object | None] = []

    result = runtime.run(
        run_id="run-runtime-gated-missing",
        action=action,
        executor=lambda checkpoint: executions.append(checkpoint),
        control_plane_approvals=lifecycle,
        approval_key=b"runtime-gate-test-key",
    )

    assert result.state == JournalState.FAILED
    assert result.reason == JournalReason.APPROVAL_BLOCK
    assert "approval_id missing" in (result.error or "")
    assert executions == []
    assert [entry.state for entry in runtime.history("run-runtime-gated-missing")] == [
        JournalState.PROPOSED,
        JournalState.FAILED,
    ]


def test_control_plane_gate_consumes_valid_approval_before_write_execution(tmp_path) -> None:
    key = b"runtime-gate-test-key"
    now = datetime(2026, 5, 3, 5, 30, tzinfo=timezone.utc)
    runtime = make_runtime(tmp_path)
    lifecycle = ApprovalLifecycleStore(key)
    lifecycle.approve(
        lifecycle.request(
            approval_id="approval-runtime-1",
            requested_by="tester",
            action="demo",
            subject="system",
            created_at=now,
        ).approval_id,
        now=now,
    )
    action = make_action(EffectClass.REVERSIBLE_WRITE, key="runtime-gated-approved")
    executions: list[object | None] = []

    result = runtime.run(
        run_id="run-runtime-gated-approved",
        action=action,
        executor=lambda checkpoint: executions.append(checkpoint),
        control_plane_approvals=lifecycle,
        approval_id="approval-runtime-1",
        approval_key=key,
        approval_now=now,
    )

    assert result.state == JournalState.APPLIED
    assert executions == [None]
    assert lifecycle.get("approval-runtime-1").status == EXECUTED
    assert [entry.state for entry in runtime.history("run-runtime-gated-approved")] == [
        JournalState.PROPOSED,
        JournalState.APPROVED,
        JournalState.EXECUTING,
        JournalState.APPLIED,
    ]


def test_control_plane_completion_failure_is_not_reported_as_pre_execution_block(tmp_path) -> None:
    key = b"runtime-gate-test-key"
    now = datetime(2026, 5, 3, 5, 32, tzinfo=timezone.utc)
    runtime = make_runtime(tmp_path)
    lifecycle = ApprovalLifecycleStore(key)
    lifecycle.approve(
        lifecycle.request(
            approval_id="approval-runtime-complete-fail",
            requested_by="tester",
            action="demo",
            subject="system",
            created_at=now,
        ).approval_id,
        now=now,
    )
    action = make_action(EffectClass.REVERSIBLE_WRITE, key="runtime-gated-complete-fail")
    executions: list[object | None] = []

    class CompletionFailingApprovals:
        def get(self, approval_id: str):
            return lifecycle.get(approval_id)

        def reserve_for_execution(self, *args, **kwargs):
            return lifecycle.reserve_for_execution(*args, **kwargs)

        def complete_execution(self, *args, **kwargs):
            raise ApprovalValidationError("completion store unavailable")

    result = runtime.run(
        run_id="run-runtime-gated-complete-fail",
        action=action,
        executor=lambda checkpoint: executions.append(checkpoint),
        control_plane_approvals=CompletionFailingApprovals(),
        approval_id="approval-runtime-complete-fail",
        approval_key=key,
        approval_now=now,
    )

    assert executions == [None]
    assert result.state == JournalState.FAILED
    assert result.reason == JournalReason.APPROVAL_COMPLETION_ERROR
    assert [entry.state for entry in runtime.history("run-runtime-gated-complete-fail")] == [
        JournalState.PROPOSED,
        JournalState.APPROVED,
        JournalState.EXECUTING,
        JournalState.FAILED,
    ]


def test_control_plane_gate_does_not_consume_approval_when_executor_fails(tmp_path) -> None:
    key = b"runtime-gate-test-key"
    now = datetime(2026, 5, 3, 5, 35, tzinfo=timezone.utc)
    runtime = make_runtime(tmp_path)
    lifecycle = ApprovalLifecycleStore(key)
    lifecycle.approve(
        lifecycle.request(
            approval_id="approval-runtime-fail",
            requested_by="tester",
            action="demo",
            subject="system",
            created_at=now,
        ).approval_id,
        now=now,
    )
    action = make_action(EffectClass.REVERSIBLE_WRITE, key="runtime-gated-fails")

    result = runtime.run(
        run_id="run-runtime-gated-fails",
        action=action,
        executor=lambda checkpoint: (_ for _ in ()).throw(RuntimeError("boom")),
        control_plane_approvals=lifecycle,
        approval_id="approval-runtime-fail",
        approval_key=key,
        approval_now=now,
    )

    assert result.state == JournalState.FAILED
    assert result.reason == JournalReason.EXECUTION_ERROR
    assert lifecycle.get("approval-runtime-fail").status == IN_FLIGHT


def test_runtime_enforcement_rejects_lookup_only_registry_to_avoid_replay(tmp_path) -> None:
    key = b"runtime-gate-test-key"
    now = datetime(2026, 5, 3, 5, 40, tzinfo=timezone.utc)
    registry = ControlPlaneRegistry(tmp_path / "control-plane.db")
    registry.initialize()
    registry.create_approval(
        sign_approval_record(
            ApprovalRecord(
                approval_id="registry-approval",
                requested_by="tester",
                action="demo",
                subject="system",
                status="APPROVED",
                signed_payload="",
                signature="",
                created_at=now.isoformat(),
            ),
            key,
        )
    )
    runtime = make_runtime(tmp_path)
    action = make_action(EffectClass.REVERSIBLE_WRITE, key="runtime-registry-replay")
    executions: list[object | None] = []

    result = runtime.run(
        run_id="run-runtime-registry-replay",
        action=action,
        executor=lambda checkpoint: executions.append(checkpoint),
        control_plane_approvals=registry,
        approval_id="registry-approval",
        approval_key=key,
        approval_now=now,
    )

    assert result.state == JournalState.FAILED
    assert result.reason == JournalReason.APPROVAL_BLOCK
    assert "lifecycle approval store" in (result.error or "")
    assert executions == []


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
    assert result.reason == JournalReason.HANDOFF_REQUESTED
    assert result.error is None
    assert executed == []
    assert runtime.checkpoint_for("run-handoff") is None
    assert [entry.state for entry in runtime.history("run-handoff")] == [
        JournalState.PROPOSED,
        JournalState.APPROVED,
        JournalState.HANDED_OFF,
    ]
    assert runtime.history("run-handoff")[-1].reason == JournalReason.HANDOFF_REQUESTED


def test_escalated_control_plane_gated_action_hands_off_without_reserving_approval(tmp_path) -> None:
    key = b"runtime-gate-test-key"
    now = datetime(2026, 5, 3, 5, 45, tzinfo=timezone.utc)
    runtime = make_runtime(tmp_path)
    lifecycle = ApprovalLifecycleStore(key)
    lifecycle.approve(
        lifecycle.request(
            approval_id="approval-runtime-handoff",
            requested_by="tester",
            action="demo",
            subject="system",
            created_at=now,
        ).approval_id,
        now=now,
    )
    approvals = ApprovalHookRegistry()
    approvals.register(lambda envelope: ApprovalDecision.ESCALATE)
    action = make_action(EffectClass.IRREVERSIBLE_WRITE, key="runtime-gated-handoff")
    executed: list[object | None] = []

    result = runtime.run(
        run_id="run-runtime-gated-handoff",
        action=action,
        executor=lambda checkpoint: executed.append(checkpoint),
        approval_hooks=approvals,
        control_plane_approvals=lifecycle,
        approval_id="approval-runtime-handoff",
        approval_key=key,
        approval_now=now,
    )

    assert result.state == JournalState.HANDED_OFF
    assert executed == []
    assert lifecycle.get("approval-runtime-handoff").status == "APPROVED"


def test_escalated_action_does_not_run_compensation_hooks(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    action = make_action(EffectClass.COMPENSATABLE_WRITE, key="handoff-comp")
    approvals = ApprovalHookRegistry()
    approvals.register(lambda envelope: ApprovalDecision.ESCALATE)
    compensations: list[str] = []
    hooks = CompensationHookRegistry()
    hooks.register(lambda envelope, error: compensations.append("called"))

    result = runtime.run(
        run_id="run-handoff-comp",
        action=action,
        executor=lambda checkpoint: (_ for _ in ()).throw(RuntimeError("should not execute")),
        approval_hooks=approvals,
        compensation_hooks=hooks,
    )

    assert result.state == JournalState.HANDED_OFF
    assert result.reason == JournalReason.HANDOFF_REQUESTED
    assert compensations == []


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
    assert result.reason == JournalReason.APPROVAL_BLOCK
    assert result.error is None
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
    assert result.reason == JournalReason.APPROVAL_ERROR
    assert result.error == "approval failed"
    assert [entry.state for entry in runtime.history("run-approval-failure")] == [
        JournalState.PROPOSED,
        JournalState.FAILED,
    ]
    assert runtime.history("run-approval-failure")[-1].reason == JournalReason.APPROVAL_ERROR
    assert runtime.history("run-approval-failure")[-1].error == "approval failed"


def test_compensation_hook_failure_marks_run_compensation_failed(tmp_path) -> None:
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

    assert result.state == JournalState.COMPENSATION_FAILED
    assert result.reason == JournalReason.COMPENSATION_ERROR
    assert result.error == "comp failed"
    history = runtime.history("run-compensation-failure")
    assert [entry.state for entry in history] == [
        JournalState.PROPOSED,
        JournalState.APPROVED,
        JournalState.EXECUTING,
        JournalState.COMPENSATING,
        JournalState.COMPENSATION_FAILED,
    ]
    assert history[3].reason == JournalReason.EXECUTION_ERROR
    assert history[3].error == "boom"
    assert history[4].reason == JournalReason.COMPENSATION_ERROR
    assert history[4].error == "comp failed"


def test_non_compensatable_execution_failure_stays_failed(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    action = make_action(EffectClass.REVERSIBLE_WRITE, key="exec-failure")

    result = runtime.run(
        run_id="run-execution-failure",
        action=action,
        executor=lambda checkpoint: (_ for _ in ()).throw(RuntimeError("executor boom")),
    )

    assert result.state == JournalState.FAILED
    assert result.reason == JournalReason.EXECUTION_ERROR
    assert result.error == "executor boom"
    history = runtime.history("run-execution-failure")
    assert [entry.state for entry in history] == [
        JournalState.PROPOSED,
        JournalState.APPROVED,
        JournalState.EXECUTING,
        JournalState.FAILED,
    ]
    assert history[-1].reason == JournalReason.EXECUTION_ERROR
    assert history[-1].error == "executor boom"


def test_compensation_terminal_contract_matches_overnight_oracle_expectations(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    action = make_action(EffectClass.COMPENSATABLE_WRITE, key="comp-terminal-contract")
    hooks = CompensationHookRegistry()
    hooks.register(lambda envelope, error: None)

    result = runtime.run(
        run_id="run-compensation-terminal-contract",
        action=action,
        executor=lambda checkpoint: (_ for _ in ()).throw(RuntimeError("boom")),
        compensation_hooks=hooks,
    )

    assert result.state == JournalState.COMPENSATED
    assert result.state.value == "compensated"


def test_compensation_failure_terminal_contract_is_explicit_state(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    action = make_action(EffectClass.COMPENSATABLE_WRITE, key="comp-failed-terminal-contract")
    hooks = CompensationHookRegistry()
    hooks.register(lambda envelope, error: (_ for _ in ()).throw(RuntimeError("cleanup broke")))

    result = runtime.run(
        run_id="run-compensation-failed-terminal-contract",
        action=action,
        executor=lambda checkpoint: (_ for _ in ()).throw(RuntimeError("boom")),
        compensation_hooks=hooks,
    )

    assert result.state == JournalState.COMPENSATION_FAILED
    assert result.state.value == "compensation_failed"
    assert result.reason == JournalReason.COMPENSATION_ERROR


def test_unsupported_rollback_expectation_maps_to_runtime_terminal_states(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    action = ActionEnvelope(
        name="unsupported-rollback-expectation",
        target="external-system",
        args={"expects": "full rollback"},
        diff="",
        actor="tester",
        privileges=[],
        idempotency_key="unsupported-rollback-terminal-contract",
        effect=EffectClass.COMPENSATABLE_WRITE,
    )

    compensated_hooks = CompensationHookRegistry()
    compensated_hooks.register(lambda envelope, error: None)
    compensated = runtime.run(
        run_id="run-unsupported-rollback-compensated",
        action=action,
        executor=lambda checkpoint: (_ for _ in ()).throw(RuntimeError("undo requested")),
        compensation_hooks=compensated_hooks,
    )

    failed_hooks = CompensationHookRegistry()
    failed_hooks.register(lambda envelope, error: (_ for _ in ()).throw(RuntimeError("undo unavailable")))
    failed = runtime.run(
        run_id="run-unsupported-rollback-failed",
        action=action.model_copy(update={"idempotency_key": "unsupported-rollback-terminal-contract-failed"}),
        executor=lambda checkpoint: (_ for _ in ()).throw(RuntimeError("undo requested")),
        compensation_hooks=failed_hooks,
    )

    assert compensated.state == JournalState.COMPENSATED
    assert failed.state == JournalState.COMPENSATION_FAILED


def test_compensating_can_fail_terminally() -> None:
    validate_transition(JournalState.COMPENSATING, JournalState.COMPENSATION_FAILED)


def test_resumed_compensation_clears_checkpoint_on_terminal_completion(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    action = make_action(EffectClass.COMPENSATABLE_WRITE, key="resume-comp-terminal")
    hooks = CompensationHookRegistry()
    hooks.register(lambda envelope, error: None)

    first_result = runtime.run(
        run_id="run-resume-compensated",
        action=action,
        executor=lambda checkpoint: (_ for _ in ()).throw(ResumableExecution({"step": 1})),
    )
    second_result = runtime.run(
        run_id="run-resume-compensated",
        action=action,
        executor=lambda checkpoint: (_ for _ in ()).throw(RuntimeError("boom after resume")),
        compensation_hooks=hooks,
    )

    assert first_result.state == JournalState.RESUMABLE
    assert second_result.state == JournalState.COMPENSATED
    assert runtime.checkpoint_for("run-resume-compensated") is None


def test_repeated_resumable_updates_checkpoint_and_journal_shape(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    action = make_action(EffectClass.REVERSIBLE_WRITE, key="resume-twice")
    checkpoints_seen: list[object | None] = []

    def executor(checkpoint: object | None) -> dict[str, bool]:
        checkpoints_seen.append(checkpoint)
        if checkpoint is None:
            raise ResumableExecution({"step": 1})
        if checkpoint == {"step": 1}:
            raise ResumableExecution({"step": 2})
        return {"done": True}

    first = runtime.run(run_id="run-resume-twice", action=action, executor=executor)
    second = runtime.run(run_id="run-resume-twice", action=action, executor=executor)
    third = runtime.run(run_id="run-resume-twice", action=action, executor=executor)

    assert [result.state for result in [first, second, third]] == [
        JournalState.RESUMABLE,
        JournalState.RESUMABLE,
        JournalState.APPLIED,
    ]
    assert checkpoints_seen == [None, {"step": 1}, {"step": 2}]
    assert runtime.checkpoint_for("run-resume-twice") is None
    assert [entry.state for entry in runtime.history("run-resume-twice")] == [
        JournalState.PROPOSED,
        JournalState.APPROVED,
        JournalState.EXECUTING,
        JournalState.RESUMABLE,
        JournalState.EXECUTING,
        JournalState.RESUMABLE,
        JournalState.EXECUTING,
        JournalState.APPLIED,
    ]


def test_terminal_rerun_is_idempotent_and_does_not_reexecute(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    action = make_action(EffectClass.REVERSIBLE_WRITE, key="terminal-rerun")
    executions: list[object | None] = []

    first = runtime.run(
        run_id="run-terminal-rerun",
        action=action,
        executor=lambda checkpoint: executions.append(checkpoint) or {"ok": True},
    )
    second = runtime.run(
        run_id="run-terminal-rerun",
        action=action,
        executor=lambda checkpoint: (_ for _ in ()).throw(RuntimeError("should not reexecute")),
    )

    assert first.state == JournalState.APPLIED
    assert second.state == JournalState.APPLIED
    assert executions == [None]
    assert len(runtime.history("run-terminal-rerun")) == 4


def test_resumable_run_without_live_checkpoint_is_idempotent(tmp_path) -> None:
    storage_path = tmp_path / "journal.jsonl"
    action = make_action(EffectClass.REVERSIBLE_WRITE, key="resume-after-reload")

    runtime = Runtime(storage_path)
    runtime.run(
        run_id="run-resume-after-reload",
        action=action,
        executor=lambda checkpoint: (_ for _ in ()).throw(ResumableExecution({"step": 1})),
    )

    reloaded_runtime = Runtime(storage_path)
    executions: list[object | None] = []
    result = reloaded_runtime.run(
        run_id="run-resume-after-reload",
        action=action,
        executor=lambda checkpoint: executions.append(checkpoint) or {"ok": True},
    )

    assert result.state == JournalState.RESUMABLE
    assert executions == []
    assert [entry.state for entry in reloaded_runtime.history("run-resume-after-reload")] == [
        JournalState.PROPOSED,
        JournalState.APPROVED,
        JournalState.EXECUTING,
        JournalState.RESUMABLE,
    ]


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
