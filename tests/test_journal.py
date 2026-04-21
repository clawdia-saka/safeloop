import pytest
from pydantic import ValidationError

from safeloop.journal import JournalEntry, JournalState, validate_transition


EXPECTED_STATES = {
    "proposed",
    "approved",
    "executing",
    "applied",
    "compensating",
    "compensated",
    "compensation_failed",
    "failed",
    "resumable",
    "handed_off",
}


def test_journal_state_enum_values() -> None:
    assert {state.value for state in JournalState} == EXPECTED_STATES


def test_journal_entry_round_trip() -> None:
    entry = JournalEntry(
        run_id="run-123",
        action_id="action-456",
        state=JournalState.PROPOSED,
    )

    assert entry.run_id == "run-123"
    assert entry.action_id == "action-456"
    assert entry.state == JournalState.PROPOSED


def test_journal_entry_requires_all_fields() -> None:
    with pytest.raises(ValidationError) as excinfo:
        JournalEntry()

    errors = {error["loc"][0] for error in excinfo.value.errors()}
    assert errors == {"run_id", "action_id", "state"}


@pytest.mark.parametrize(
    ("current_state", "next_state"),
    [
        (JournalState.PROPOSED, JournalState.APPROVED),
        (JournalState.PROPOSED, JournalState.FAILED),
        (JournalState.APPROVED, JournalState.EXECUTING),
        (JournalState.APPROVED, JournalState.HANDED_OFF),
        (JournalState.EXECUTING, JournalState.APPLIED),
        (JournalState.EXECUTING, JournalState.COMPENSATING),
        (JournalState.EXECUTING, JournalState.FAILED),
        (JournalState.EXECUTING, JournalState.RESUMABLE),
        (JournalState.COMPENSATING, JournalState.COMPENSATED),
        (JournalState.COMPENSATING, JournalState.COMPENSATION_FAILED),
        (JournalState.RESUMABLE, JournalState.EXECUTING),
    ],
)
def test_validate_transition_accepts_valid_transitions(
    current_state: JournalState,
    next_state: JournalState,
) -> None:
    validate_transition(current_state, next_state)


@pytest.mark.parametrize(
    ("current_state", "next_state"),
    [
        (JournalState.PROPOSED, JournalState.APPLIED),
        (JournalState.APPROVED, JournalState.COMPENSATED),
        (JournalState.APPLIED, JournalState.EXECUTING),
        (JournalState.FAILED, JournalState.EXECUTING),
        (JournalState.HANDED_OFF, JournalState.EXECUTING),
        (JournalState.EXECUTING, JournalState.HANDED_OFF),
        (JournalState.COMPENSATED, JournalState.APPLIED),
    ],
)
def test_validate_transition_rejects_invalid_transitions(
    current_state: JournalState,
    next_state: JournalState,
) -> None:
    with pytest.raises(ValueError, match="Invalid journal transition"):
        validate_transition(current_state, next_state)
