from enum import Enum

from pydantic import BaseModel


class JournalState(str, Enum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    EXECUTING = "executing"
    APPLIED = "applied"
    COMPENSATING = "compensating"
    COMPENSATED = "compensated"
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
        JournalState.HANDED_OFF,
    },
    JournalState.COMPENSATING: {JournalState.COMPENSATED},
    JournalState.RESUMABLE: {JournalState.EXECUTING},
    JournalState.APPLIED: set(),
    JournalState.COMPENSATED: set(),
    JournalState.FAILED: set(),
    JournalState.HANDED_OFF: set(),
}


class JournalEntry(BaseModel):
    run_id: str
    action_id: str
    state: JournalState


def validate_transition(current_state: JournalState, next_state: JournalState) -> None:
    allowed_next_states = _ALLOWED_TRANSITIONS[current_state]
    if next_state not in allowed_next_states:
        raise ValueError(
            f"Invalid journal transition: {current_state.value} -> {next_state.value}"
        )
