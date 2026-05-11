from pathlib import Path

from safeloop.api import RunDetail, RunSummary, TerminalBoundary, TerminalSemantics
from safeloop.journal import JournalReason


SPEC_PATH = Path("docs/specs/state-machine-and-journal-schema.md")


def read_spec() -> str:
    return SPEC_PATH.read_text(encoding="utf-8")


def test_state_machine_spec_documents_terminal_semantics_payload() -> None:
    spec = read_spec()

    required_markers = [
        "`terminal_semantics`",
        "`terminal_state`",
        "`expected_terminal_state`",
        "`boundary`",
        "`scope_guess`",
        "`note`",
        "`TerminalBoundary`",
        "storage-only viewers cannot resume",
    ]

    for marker in required_markers:
        assert marker in spec

    for field_name in TerminalSemantics.model_fields:
        assert f"`{field_name}`" in spec

    for boundary in TerminalBoundary:
        assert f"`{boundary.value}`" in spec


def test_state_machine_spec_documents_api_models_against_code() -> None:
    spec = read_spec()

    for model in (RunSummary, RunDetail):
        for field_name in model.model_fields:
            assert f'"{field_name}"' in spec or f"`{field_name}`" in spec

    assert "Run summary" in spec
    assert "Run detail" in spec
    assert "Journal entry payload" in spec


def test_state_machine_spec_makes_compensation_vs_rollback_unambiguous() -> None:
    spec = read_spec()

    required_markers = [
        "Compensation is not rollback",
        "mitigation rather than proof of perfect rollback",
        "`compensation_failed` is not rollback",
        "successful compensation does not prove perfect rollback",
    ]

    for marker in required_markers:
        assert marker in spec


def test_state_machine_spec_documents_api_shape_after_terminal_semantics() -> None:
    spec = read_spec()

    required_markers = [
        '"terminal_semantics"',
        '"terminal_state": "applied"',
        '"boundary": "applied"',
        '"scope_guess": "inside_mvp_scope"',
    ]

    for marker in required_markers:
        assert marker in spec


def test_state_machine_spec_documents_compensation_failure_operator_semantics() -> None:
    spec = read_spec()

    required_markers = [
        "Compensation failure operator semantics",
        "the `compensating` entry preserves the original executor error",
        "the terminal `compensation_failed` entry preserves the compensation hook error",
        "do not retry automatically from `compensation_failed`",
        "operator-owned recovery",
    ]
    for marker in required_markers:
        assert marker in spec


def test_state_machine_spec_documents_every_journal_reason() -> None:
    spec = read_spec()

    for reason in JournalReason:
        assert f"`{reason.value}`" in spec or reason.value in spec
