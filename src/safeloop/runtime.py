"""Runtime primitives backed by journal storage."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from safeloop.control_plane.gate import ApprovalGateError
from safeloop.control_plane.lifecycle import ApprovalValidationError
from safeloop.control_plane.registry import ApprovalRecord
from safeloop.hooks import (
    ApprovalDecision,
    ApprovalHookRegistry,
    CompensationHookRegistry,
)
from safeloop.journal import JournalEntry, JournalReason, JournalState, validate_transition
from safeloop.storage import LocalJournalStorage
from safeloop.types import ActionEnvelope, EffectClass

Executor = Callable[[object | None], Any]


class ResumableExecution(Exception):
    """Signal that execution should pause and be resumed from a checkpoint."""

    def __init__(self, checkpoint: object) -> None:
        super().__init__("execution paused")
        self.checkpoint = checkpoint


@dataclass(frozen=True, slots=True)
class RunRecord:
    run_id: str
    action_id: str
    state: JournalState
    journal: list[JournalEntry]


class Runtime:
    """Minimal in-process runtime with persistent journaling."""

    def __init__(self, storage: LocalJournalStorage | str | Path) -> None:
        if isinstance(storage, LocalJournalStorage):
            self.storage = storage
        else:
            self.storage = LocalJournalStorage(storage)
        self._checkpoints: dict[str, object] = {}

    def history(self, run_id: str) -> list[JournalEntry]:
        return self.storage.read(run_id)

    def checkpoint_for(self, run_id: str) -> object | None:
        return self._checkpoints.get(run_id)

    def list_runs(self) -> list[RunRecord]:
        records: list[RunRecord] = []
        for run_id in self.storage.list_run_ids():
            record = self.get_run(run_id)
            if record is not None:
                records.append(record)
        return records

    def get_run(self, run_id: str) -> RunRecord | None:
        journal = self.history(run_id)
        if not journal:
            return None
        latest = journal[-1]
        return RunRecord(
            run_id=latest.run_id,
            action_id=latest.action_id,
            state=latest.state,
            journal=journal,
        )

    def run(
        self,
        *,
        run_id: str,
        action: ActionEnvelope,
        executor: Executor,
        approval_hooks: ApprovalHookRegistry | None = None,
        compensation_hooks: CompensationHookRegistry | None = None,
        control_plane_approvals: object | None = None,
        approval_id: str | None = None,
        approval_key: bytes | None = None,
        approval_now: datetime | None = None,
    ) -> JournalEntry:
        current = self._current(run_id)
        if current is not None and current.action_id != action.idempotency_key:
            raise ValueError(
                f"run_id {run_id!r} is already bound to action {current.action_id!r}"
            )

        if current is None:
            self._append(run_id, action, JournalState.PROPOSED)
            control_plane_approval: ApprovalRecord | None = None
            try:
                decision = self._approval_decision(action, approval_hooks)
            except Exception as exc:
                return self._append(
                    run_id,
                    action,
                    JournalState.FAILED,
                    reason=JournalReason.APPROVAL_ERROR,
                    error=str(exc),
                )
            if decision is ApprovalDecision.BLOCK:
                return self._append(run_id, action, JournalState.FAILED, reason=JournalReason.APPROVAL_BLOCK)
            if decision is ApprovalDecision.ESCALATE:
                self._append(run_id, action, JournalState.APPROVED)
                return self._append(
                    run_id,
                    action,
                    JournalState.HANDED_OFF,
                    reason=JournalReason.HANDOFF_REQUESTED,
                )
            try:
                control_plane_approval = self._control_plane_gate(
                    action,
                    approvals=control_plane_approvals,
                    approval_id=approval_id,
                    approval_key=approval_key,
                    now=approval_now,
                )
            except ApprovalGateError as exc:
                return self._append(
                    run_id,
                    action,
                    JournalState.FAILED,
                    reason=JournalReason.APPROVAL_BLOCK,
                    error=str(exc),
                )
            self._append(run_id, action, JournalState.APPROVED)
            self._append(run_id, action, JournalState.EXECUTING)
        elif current.state == JournalState.RESUMABLE:
            control_plane_approval = None
            checkpoint = self._checkpoints.get(run_id)
            if checkpoint is None:
                # The persisted journal says the run paused earlier, but this
                # runtime instance no longer has the live checkpoint payload
                # needed to resume safely. Keep the run idempotently resumable
                # in the journal without blindly re-executing from None.
                return current
            try:
                control_plane_approval = self._resume_control_plane_gate(
                    action,
                    approvals=control_plane_approvals,
                    approval_id=approval_id,
                    approval_key=approval_key,
                )
            except ApprovalGateError as exc:
                return self._append(
                    run_id,
                    action,
                    JournalState.FAILED,
                    reason=JournalReason.APPROVAL_BLOCK,
                    error=str(exc),
                )
            self._append(run_id, action, JournalState.EXECUTING)
        else:
            return current

        checkpoint = self._checkpoints.get(run_id)
        try:
            executor(checkpoint)
        except ResumableExecution as exc:
            self._checkpoints[run_id] = exc.checkpoint
            return self._append(run_id, action, JournalState.RESUMABLE)
        except Exception as exc:
            if compensation_hooks is not None and action.effect is EffectClass.COMPENSATABLE_WRITE:
                self._append(
                    run_id,
                    action,
                    JournalState.COMPENSATING,
                    reason=JournalReason.EXECUTION_ERROR,
                    error=str(exc),
                )
                try:
                    compensation_hooks.run(action, exc)
                except Exception as compensation_exc:
                    self._checkpoints.pop(run_id, None)
                    return self._append(
                        run_id,
                        action,
                        JournalState.COMPENSATION_FAILED,
                        reason=JournalReason.COMPENSATION_ERROR,
                        error=str(compensation_exc),
                    )
                self._checkpoints.pop(run_id, None)
                return self._append(run_id, action, JournalState.COMPENSATED)
            self._checkpoints.pop(run_id, None)
            return self._append(
                run_id,
                action,
                JournalState.FAILED,
                reason=JournalReason.EXECUTION_ERROR,
                error=str(exc),
            )

        self._checkpoints.pop(run_id, None)
        try:
            self._consume_control_plane_approval(
                control_plane_approval,
                approvals=control_plane_approvals,
                now=approval_now,
            )
        except ApprovalGateError as exc:
            return self._append(
                run_id,
                action,
                JournalState.FAILED,
                reason=JournalReason.APPROVAL_COMPLETION_ERROR,
                error=str(exc),
            )
        return self._append(run_id, action, JournalState.APPLIED)

    def _approval_decision(
        self,
        action: ActionEnvelope,
        approval_hooks: ApprovalHookRegistry | None,
    ) -> ApprovalDecision:
        if action.effect is EffectClass.READ_ONLY or approval_hooks is None:
            return ApprovalDecision.ALLOW
        return approval_hooks.evaluate(action)

    def _control_plane_gate(
        self,
        action: ActionEnvelope,
        *,
        approvals: object | None,
        approval_id: str | None,
        approval_key: bytes | None,
        now: datetime | None,
    ) -> ApprovalRecord | None:
        if action.effect is EffectClass.READ_ONLY or approvals is None:
            return None
        if approval_key is None:
            raise ApprovalGateError("approval_key missing")
        if not approval_id:
            raise ApprovalGateError("approval_id missing")
        if not (hasattr(approvals, "get") and hasattr(approvals, "reserve_for_execution") and hasattr(approvals, "complete_execution")):
            raise ApprovalGateError("runtime enforcement requires lifecycle approval store")
        record = approvals.get(approval_id)  # type: ignore[attr-defined]
        if record is None:
            raise ApprovalGateError("approval_id missing or unknown")
        try:
            return approvals.reserve_for_execution(  # type: ignore[attr-defined]
                record,
                requested_by=record.requested_by,
                action=action.name,
                subject=action.target,
                now=now if now is not None else datetime.now().astimezone(),
            )
        except ApprovalValidationError as exc:
            raise ApprovalGateError(str(exc)) from exc

    def _consume_control_plane_approval(
        self,
        approval: ApprovalRecord | None,
        *,
        approvals: object | None,
        now: datetime | None,
    ) -> None:
        if approval is None:
            return
        try:
            approvals.complete_execution(  # type: ignore[union-attr]
                approval,
                requested_by=approval.requested_by,
                action=approval.action,
                subject=approval.subject,
                now=now if now is not None else datetime.now().astimezone(),
            )
        except ApprovalValidationError as exc:
            raise ApprovalGateError(str(exc)) from exc

    def _resume_control_plane_gate(
        self,
        action: ActionEnvelope,
        *,
        approvals: object | None,
        approval_id: str | None,
        approval_key: bytes | None,
    ) -> ApprovalRecord | None:
        if action.effect is EffectClass.READ_ONLY or approvals is None:
            return None
        if approval_key is None:
            raise ApprovalGateError("approval_key missing")
        if not approval_id:
            raise ApprovalGateError("approval_id missing")
        if not (hasattr(approvals, "get") and hasattr(approvals, "validate_in_flight_for_resume") and hasattr(approvals, "complete_execution")):
            raise ApprovalGateError("runtime enforcement requires lifecycle approval store")
        record = approvals.get(approval_id)  # type: ignore[attr-defined]
        if record is None:
            raise ApprovalGateError("approval_id missing or unknown")
        try:
            return approvals.validate_in_flight_for_resume(  # type: ignore[attr-defined]
                record,
                action=action.name,
                subject=action.target,
                now=datetime.now().astimezone(),
            )
        except ApprovalValidationError as exc:
            raise ApprovalGateError(str(exc)) from exc

    def _current(self, run_id: str) -> JournalEntry | None:
        history = self.history(run_id)
        if not history:
            return None
        return history[-1]

    def _append(
        self,
        run_id: str,
        action: ActionEnvelope,
        state: JournalState,
        *,
        reason: JournalReason | None = None,
        error: str | None = None,
    ) -> JournalEntry:
        history = self.history(run_id)
        if history:
            validate_transition(history[-1].state, state)
        entry = JournalEntry(
            run_id=run_id,
            action_id=action.idempotency_key,
            state=state,
            reason=reason,
            error=error,
        )
        self.storage.append(entry)
        return entry
