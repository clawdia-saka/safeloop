"""Runtime primitives backed by journal storage."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

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
    ) -> JournalEntry:
        current = self._current(run_id)
        if current is not None and current.action_id != action.idempotency_key:
            raise ValueError(
                f"run_id {run_id!r} is already bound to action {current.action_id!r}"
            )

        if current is None:
            self._append(run_id, action, JournalState.PROPOSED)
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
            self._append(run_id, action, JournalState.APPROVED)
            if decision is ApprovalDecision.ESCALATE:
                return self._append(
                    run_id,
                    action,
                    JournalState.HANDED_OFF,
                    reason=JournalReason.HANDOFF_REQUESTED,
                )
            self._append(run_id, action, JournalState.EXECUTING)
        elif current.state == JournalState.RESUMABLE:
            checkpoint = self._checkpoints.get(run_id)
            if checkpoint is None:
                # The persisted journal says the run paused earlier, but this
                # runtime instance no longer has the live checkpoint payload
                # needed to resume safely. Keep the run idempotently resumable
                # in the journal without blindly re-executing from None.
                return current
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
        return self._append(run_id, action, JournalState.APPLIED)

    def _approval_decision(
        self,
        action: ActionEnvelope,
        approval_hooks: ApprovalHookRegistry | None,
    ) -> ApprovalDecision:
        if action.effect is EffectClass.READ_ONLY or approval_hooks is None:
            return ApprovalDecision.ALLOW
        return approval_hooks.evaluate(action)

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
