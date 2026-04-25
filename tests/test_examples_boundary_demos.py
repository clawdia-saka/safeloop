from pathlib import Path
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from examples.boundary_demos import (
    describe_unsupported_rollback_expectation,
    run_compensation_failed_demo,
    run_handoff_demo,
    run_repeated_resume_demo,
    run_resumable_demo,
)
from safeloop.api import RunViewer, create_app
from safeloop.journal import JournalReason, JournalState


def test_run_handoff_demo_stops_before_execution() -> None:
    result = run_handoff_demo()

    assert result.classification == "boundary"
    assert result.final_state is JournalState.HANDED_OFF
    assert result.final_reason is JournalReason.HANDOFF_REQUESTED
    assert result.executor_called is False
    assert result.error is None
    assert result.journal_states == [
        JournalState.PROPOSED,
        JournalState.APPROVED,
        JournalState.HANDED_OFF,
    ]


def test_run_compensation_failed_demo_reports_terminal_boundary_state() -> None:
    result = run_compensation_failed_demo()

    assert result.classification == "boundary"
    assert result.final_state is JournalState.COMPENSATION_FAILED
    assert result.final_reason is JournalReason.COMPENSATION_ERROR
    assert result.executor_called is True
    assert result.error == "cleanup hook failed"
    assert result.journal_states == [
        JournalState.PROPOSED,
        JournalState.APPROVED,
        JournalState.EXECUTING,
        JournalState.COMPENSATING,
        JournalState.COMPENSATION_FAILED,
    ]


def test_run_resumable_demo_exposes_pause_then_resume_shape() -> None:
    result = run_resumable_demo()

    assert result.classification == "boundary"
    assert result.final_state is JournalState.APPLIED
    assert result.final_reason is None
    assert result.executor_called is True
    assert result.has_checkpoint_before_resume is True
    assert result.has_checkpoint_after_resume is False
    assert result.journal_states == [
        JournalState.PROPOSED,
        JournalState.APPROVED,
        JournalState.EXECUTING,
        JournalState.RESUMABLE,
        JournalState.EXECUTING,
        JournalState.APPLIED,
    ]


def test_boundary_demo_persists_runtime_truth_for_viewer_and_api(tmp_path) -> None:
    storage_path = tmp_path / "journal.jsonl"
    result = run_handoff_demo(storage_path=storage_path)

    viewer_run = RunViewer(storage_path).get_run("boundary-demo:handoff")
    api_response = TestClient(create_app(storage_path)).get("/runs/boundary-demo:handoff")

    assert viewer_run is not None
    assert result.final_state is JournalState.HANDED_OFF
    assert viewer_run.state == JournalState.HANDED_OFF.value
    assert viewer_run.journal[-1].reason == JournalReason.HANDOFF_REQUESTED.value
    assert api_response.status_code == 200
    assert api_response.json() == viewer_run.model_dump(mode="json", exclude_none=True)


def test_repeated_resume_demo_persists_each_checkpoint_for_viewer_and_api(tmp_path) -> None:
    storage_path = tmp_path / "journal.jsonl"
    result = run_repeated_resume_demo(storage_path=storage_path)

    viewer_run = RunViewer(storage_path).get_run("boundary-demo:repeated-resume")
    api_response = TestClient(create_app(storage_path)).get("/runs/boundary-demo:repeated-resume")

    assert viewer_run is not None
    assert result.final_state is JournalState.APPLIED
    assert [entry.state for entry in viewer_run.journal].count(JournalState.RESUMABLE.value) == 2
    assert viewer_run.state == JournalState.APPLIED.value
    assert api_response.status_code == 200
    assert api_response.json() == viewer_run.model_dump(mode="json", exclude_none=True)


def test_repeated_resume_demo_is_documented_as_first_class_boundary_example() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    matrix = (repo_root / "docs/case-studies/boundary-scenarios.md").read_text()
    faq = (repo_root / "docs/faq.md").read_text()
    readme = (repo_root / "README.md").read_text()

    assert "Repeated resume / resumable -> resumable -> applied" in matrix
    assert "`run_repeated_resume_demo()`" in matrix
    assert "repeated_resume" in faq
    assert "repeated resume" in readme


def test_run_repeated_resume_demo_exposes_two_resume_checkpoints_before_completion() -> None:
    result = run_repeated_resume_demo()

    assert result.scenario == "repeated_resume"
    assert result.classification == "boundary"
    assert result.final_state is JournalState.APPLIED
    assert result.final_reason is None
    assert result.executor_called is True
    assert result.has_checkpoint_before_resume is True
    assert result.has_checkpoint_after_resume is False
    assert result.journal_states == [
        JournalState.PROPOSED,
        JournalState.APPROVED,
        JournalState.EXECUTING,
        JournalState.RESUMABLE,
        JournalState.EXECUTING,
        JournalState.RESUMABLE,
        JournalState.EXECUTING,
        JournalState.APPLIED,
    ]


def test_describe_unsupported_rollback_expectation_points_to_docs_only_surface() -> None:
    result = describe_unsupported_rollback_expectation()
    root = Path(__file__).resolve().parents[1]

    assert result.scenario == "unsupported_rollback_expectation"
    assert result.classification == "unsupported"
    assert result.doc_paths == [
        "docs/case-studies/boundary-scenarios.md",
        "docs/faq.md",
        "docs/case-studies/github-pr-demo.md",
    ]
    assert "Compensation should not be misread as perfect rollback" in result.summary

    for relative_path in result.doc_paths:
        doc_text = (root / relative_path).read_text()
        assert "rollback" in doc_text.lower()
