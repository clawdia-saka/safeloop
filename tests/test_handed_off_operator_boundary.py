from __future__ import annotations

from safeloop.hooks import ApprovalDecision, ApprovalHookRegistry
from safeloop.journal import JournalReason, JournalState
from safeloop.runtime import Runtime
from safeloop.types import ActionEnvelope, EffectClass
from safeloop.viewer import (
    BoundaryAnnotation,
    ScopeAnnotation,
    TerminalBoundary,
    derive_annotations,
    derive_terminal_semantics,
)


def make_write_action() -> ActionEnvelope:
    return ActionEnvelope(
        name="deploy",
        target="prod",
        args={},
        diff="",
        actor="tester",
        privileges=["write"],
        idempotency_key="handoff-action",
        effect=EffectClass.REVERSIBLE_WRITE,
    )


def test_handed_off_is_terminal_pre_execution_operator_boundary(tmp_path) -> None:
    runtime = Runtime(tmp_path / "journal.jsonl")
    approvals = ApprovalHookRegistry()
    approvals.register(lambda _action: ApprovalDecision.ESCALATE)
    action = make_write_action()
    executions: list[object | None] = []

    first = runtime.run(
        run_id="run-handoff",
        action=action,
        executor=lambda checkpoint: executions.append(checkpoint),
        approval_hooks=approvals,
    )
    second = runtime.run(
        run_id="run-handoff",
        action=action,
        executor=lambda checkpoint: executions.append(checkpoint),
        approval_hooks=approvals,
    )

    assert first.state == JournalState.HANDED_OFF
    assert first.reason == JournalReason.HANDOFF_REQUESTED
    assert second == first
    assert executions == []
    assert runtime.checkpoint_for("run-handoff") is None
    assert [entry.state for entry in runtime.history("run-handoff")] == [
        JournalState.PROPOSED,
        JournalState.APPROVED,
        JournalState.HANDED_OFF,
    ]


def test_handed_off_terminal_semantics_name_operator_owned_non_recoverable_boundary() -> None:
    semantics = derive_terminal_semantics(JournalState.HANDED_OFF, JournalReason.HANDOFF_REQUESTED)

    assert semantics.terminal_state == JournalState.HANDED_OFF
    assert semantics.expected_terminal_state == JournalState.HANDED_OFF
    assert semantics.boundary == TerminalBoundary.OPERATOR_HANDOFF
    assert semantics.scope_guess == ScopeAnnotation.BOUNDARY_CASE
    assert "operator boundary" in semantics.note
    assert "not recoverable or resumable by the automatic runtime" in semantics.note


def test_handed_off_annotations_exclude_side_effect_and_checkpoint_claims() -> None:
    scope, boundaries = derive_annotations(JournalState.HANDED_OFF, JournalReason.HANDOFF_REQUESTED)

    assert scope == ScopeAnnotation.BOUNDARY_CASE
    assert boundaries == [
        BoundaryAnnotation.PRE_EXECUTION,
        BoundaryAnnotation.OPERATOR_OWNED,
        BoundaryAnnotation.TERMINAL,
    ]
    assert BoundaryAnnotation.SIDE_EFFECTS_POSSIBLE not in boundaries
    assert BoundaryAnnotation.CHECKPOINT_RECORDED not in boundaries
