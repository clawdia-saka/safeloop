from enum import Enum

from pydantic import BaseModel


class JournalState(str, Enum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    EXECUTING = "executing"
    APPLIED = "applied"
    COMPENSATING = "compensating"
    COMPENSATED = "compensated"
    COMPENSATION_FAILED = "compensation_failed"
    FAILED = "failed"
    RESUMABLE = "resumable"
    HANDED_OFF = "handed_off"


_ALLOWED_TRANSITIONS: dict[JournalState, set[JournalState]] = {
    JournalState.PROPOSED: {JournalState.APPROVED, JournalState.FAILED},
    JournalState.APPROVED: {JournalState.EXECUTING, JournalState.HANDED_OFF},
    JournalState.EXECUTING: {
        JournalState.APPLIED,
        JournalState.COMPENSATING,
        JournalState.FAILED,
        JournalState.RESUMABLE,
    },
    JournalState.COMPENSATING: {
        JournalState.COMPENSATED,
        JournalState.COMPENSATION_FAILED,
    },
    JournalState.RESUMABLE: {JournalState.EXECUTING, JournalState.FAILED},
    JournalState.APPLIED: set(),
    JournalState.COMPENSATED: set(),
    JournalState.COMPENSATION_FAILED: set(),
    JournalState.FAILED: set(),
    JournalState.HANDED_OFF: set(),
}


class JournalReason(str, Enum):
    APPROVAL_ERROR = "approval_error"
    APPROVAL_BLOCK = "approval_block"
    APPROVAL_COMPLETION_ERROR = "approval_completion_error"
    RESUME_APPROVAL_BLOCK = "resume_approval_block"
    HANDOFF_REQUESTED = "handoff_requested"
    EXECUTION_ERROR = "execution_error"
    COMPENSATION_ERROR = "compensation_error"


class JournalEntry(BaseModel):
    run_id: str
    action_id: str
    state: JournalState
    reason: JournalReason | str | None = None
    error: str | None = None


def validate_transition(current_state: JournalState, next_state: JournalState) -> None:
    allowed_next_states = _ALLOWED_TRANSITIONS[current_state]
    if next_state not in allowed_next_states:
        raise ValueError(
            f"Invalid journal transition: {current_state.value} -> {next_state.value}"
        )
