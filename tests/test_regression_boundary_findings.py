from __future__ import annotations

from pathlib import Path
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from examples.boundary_demos import (
    run_compensation_failed_demo,
    run_handoff_demo,
    run_repeated_resume_demo,
    run_resumable_demo,
)
from safeloop.api import BoundaryAnnotation, RunViewer, ScopeAnnotation, create_app, derive_annotations
from safeloop.journal import JournalReason, JournalState
from safeloop.runtime import Runtime


def test_regression_compensation_failed_keeps_cleanup_uncertainty_and_side_effects(tmp_path) -> None:
    storage_path = tmp_path / "journal.jsonl"
    result = run_compensation_failed_demo(storage_path=storage_path)

    viewer_run = RunViewer(storage_path).get_run("boundary-demo:compensation-failed")
    api_response = TestClient(create_app(storage_path)).get("/runs/boundary-demo:compensation-failed")

    assert result.final_state is JournalState.COMPENSATION_FAILED
    assert viewer_run is not None
    assert viewer_run.state == JournalState.COMPENSATION_FAILED
    assert viewer_run.scope is ScopeAnnotation.BOUNDARY_CASE
    assert viewer_run.boundaries == [
        BoundaryAnnotation.CLEANUP_ATTEMPTED,
        BoundaryAnnotation.CLEANUP_INCOMPLETE_OR_UNCERTAIN,
        BoundaryAnnotation.SIDE_EFFECTS_POSSIBLE,
        BoundaryAnnotation.TERMINAL,
    ]
    assert api_response.status_code == 200
    assert api_response.json()["scope"] == ScopeAnnotation.BOUNDARY_CASE.value
    assert api_response.json()["boundaries"] == [
        BoundaryAnnotation.CLEANUP_ATTEMPTED.value,
        BoundaryAnnotation.CLEANUP_INCOMPLETE_OR_UNCERTAIN.value,
        BoundaryAnnotation.SIDE_EFFECTS_POSSIBLE.value,
        BoundaryAnnotation.TERMINAL.value,
    ]


def test_regression_handoff_stays_pre_execution_operator_boundary(tmp_path) -> None:
    storage_path = tmp_path / "journal.jsonl"
    result = run_handoff_demo(storage_path=storage_path)

    viewer_run = RunViewer(storage_path).get_run("boundary-demo:handoff")
    api_response = TestClient(create_app(storage_path)).get("/runs/boundary-demo:handoff")

    assert result.executor_called is False
    assert result.final_state is JournalState.HANDED_OFF
    assert viewer_run is not None
    assert viewer_run.state == JournalState.HANDED_OFF
    assert viewer_run.scope is ScopeAnnotation.BOUNDARY_CASE
    assert viewer_run.boundaries == [
        BoundaryAnnotation.PRE_EXECUTION,
        BoundaryAnnotation.OPERATOR_OWNED,
        BoundaryAnnotation.TERMINAL,
    ]
    assert api_response.status_code == 200
    assert api_response.json()["boundaries"] == [
        BoundaryAnnotation.PRE_EXECUTION.value,
        BoundaryAnnotation.OPERATOR_OWNED.value,
        BoundaryAnnotation.TERMINAL.value,
    ]


def test_regression_repeated_resume_keeps_checkpoint_truth_local_only(tmp_path) -> None:
    storage_path = tmp_path / "journal.jsonl"
    result = run_repeated_resume_demo(storage_path=storage_path)

    runtime_view = RunViewer(Runtime(storage_path)).get_run("boundary-demo:repeated-resume")
    storage_view = RunViewer(storage_path).get_run("boundary-demo:repeated-resume")
    api_response = TestClient(create_app(storage_path)).get("/runs/boundary-demo:repeated-resume")

    assert result.final_state is JournalState.APPLIED
    assert result.has_checkpoint_before_resume is True
    assert result.has_checkpoint_after_resume is False
    assert runtime_view is not None and storage_view is not None
    assert runtime_view.state == storage_view.state == JournalState.APPLIED
    assert runtime_view.scope == storage_view.scope == ScopeAnnotation.INSIDE_MVP_SCOPE
    assert runtime_view.boundaries == storage_view.boundaries == [
        BoundaryAnnotation.SIDE_EFFECTS_POSSIBLE,
        BoundaryAnnotation.TERMINAL,
    ]
    assert runtime_view.has_checkpoint is False
    assert storage_view.has_checkpoint is False
    assert api_response.status_code == 200
    assert api_response.json()["boundaries"] == [
        BoundaryAnnotation.SIDE_EFFECTS_POSSIBLE.value,
        BoundaryAnnotation.TERMINAL.value,
    ]
    assert api_response.json()["has_checkpoint"] is False
    assert [entry.state for entry in storage_view.journal] == [
        JournalState.PROPOSED,
        JournalState.APPROVED,
        JournalState.EXECUTING,
        JournalState.RESUMABLE,
        JournalState.EXECUTING,
        JournalState.RESUMABLE,
        JournalState.EXECUTING,
        JournalState.APPLIED,
    ]


def test_regression_unsupported_rollback_expectation_stays_docs_only() -> None:
    root = Path(__file__).resolve().parents[1]
    boundary_doc = (root / "docs/case-studies/boundary-scenarios.md").read_text()
    faq_doc = (root / "docs/faq.md").read_text()

    assert "Unsupported rollback expectation" in boundary_doc
    assert "| `unsupported` |" in boundary_doc or "| `unsupported` means" in boundary_doc
    assert "Compensation should not be misread as perfect rollback" in boundary_doc
    assert "does **not** claim that external side effects were perfectly rolled back" in faq_doc
    assert all(scope.value != "outside_strict_rollback_scope" for scope in ScopeAnnotation)


def test_regression_side_effect_uncertainty_remains_conservative() -> None:
    cases = [
        (JournalState.EXECUTING, None),
        (JournalState.COMPENSATING, JournalReason.EXECUTION_ERROR),
        (JournalState.COMPENSATED, None),
        (JournalState.COMPENSATION_FAILED, JournalReason.COMPENSATION_ERROR),
        (JournalState.FAILED, JournalReason.EXECUTION_ERROR),
    ]

    for state, reason in cases:
        scope, boundaries = derive_annotations(state, reason)
        assert BoundaryAnnotation.SIDE_EFFECTS_POSSIBLE in boundaries, (state, reason, scope, boundaries)
