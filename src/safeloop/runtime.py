from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from safeloop.journal import JournalEntry, JournalState, validate_transition
from safeloop.types import ActionEnvelope, EffectClass

ApprovalHook = Callable[[ActionEnvelope], str]
CompensationHook = Callable[[ActionEnvelope, Exception], None]
Executor = Callable[[object | None], Any]


class ResumableExecution(Exception):
    """Signal that execution should pause and be resumed from a checkpoint."""

    def __init__(self, checkpoint: object) -> None:
        super().__init__("execution paused")
        self.checkpoint = checkpoint


@dataclass(slots=True)
class Runtime:
    """Minimal in-process action runtime with journaling and resume support."""

    _journal: dict[str, list[JournalEntry]] = field(default_factory=dict)
    _checkpoints: dict[str, object] = field(default_factory=dict)

    def history(self, run_id: str) -> list[JournalEntry]:
        return list(self._journal.get(run_id, []))

    def checkpoint_for(self, run_id: str) -> object | None:
        return self._checkpoints.get(run_id)

    def run(
        self,
        *,
        run_id: str,
        action: ActionEnvelope,
        executor: Executor,
        approval_hook: ApprovalHook | None = None,
        compensation_hook: CompensationHook | None = None,
    ) -> JournalEntry:
        current = self._current(run_id)
        if current is None:
            self._append(run_id, action, JournalState.PROPOSED)
            self._append(run_id, action, JournalState.APPROVED)
            current = self._append(run_id, action, JournalState.EXECUTING)
        elif current.state == JournalState.RESUMABLE:
            current = self._append(run_id, action, JournalState.EXECUTING)
        else:
            return current

        if action.effect != EffectClass.READ_ONLY and approval_hook is not None:
            decision = approval_hook(action)
            if decision == "handoff":
                return self._append(run_id, action, JournalState.HANDED_OFF)

        checkpoint = self._checkpoints.get(run_id)
        try:
            executor(checkpoint)
        except ResumableExecution as exc:
            self._checkpoints[run_id] = exc.checkpoint
            return self._append(run_id, action, JournalState.RESUMABLE)
        except Exception as exc:
            if compensation_hook is not None and action.effect == EffectClass.COMPENSATABLE_WRITE:
                self._append(run_id, action, JournalState.COMPENSATING)
                compensation_hook(action, exc)
                return self._append(run_id, action, JournalState.COMPENSATED)
            return self._append(run_id, action, JournalState.FAILED)

        return self._append(run_id, action, JournalState.APPLIED)

    def _current(self, run_id: str) -> JournalEntry | None:
        history = self._journal.get(run_id)
        if not history:
            return None
        return history[-1]

    def _append(self, run_id: str, action: ActionEnvelope, state: JournalState) -> JournalEntry:
        history = self._journal.setdefault(run_id, [])
        if history:
            validate_transition(history[-1].state, state)
        entry = JournalEntry(run_id=run_id, action_id=action.idempotency_key, state=state)
        history.append(entry)
        return entry
