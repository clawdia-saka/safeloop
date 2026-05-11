from __future__ import annotations

from pathlib import Path
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from examples.boundary_demos import (
    run_ambiguous_side_effect_demo,
    run_compensation_failed_demo,
    run_handoff_demo,
    run_resumable_demo,
)
from safeloop.api import BoundaryAnnotation, RunViewer, ScopeAnnotation, create_app, derive_annotations
from safeloop.journal import JournalReason, JournalState
from safeloop.runtime import ResumableExecution, Runtime
from safeloop.types import ActionEnvelope, EffectClass


def test_regression_ambiguous_side_effect_keeps_applied_terminal_truth_and_api_consistency(tmp_path) -> None:
    storage_path = tmp_path / "journal.jsonl"
    result = run_ambiguous_side_effect_demo(storage_path=storage_path)

    viewer_run = RunViewer(storage_path).get_run("boundary-demo:ambiguous-side-effect")
    api_response = TestClient(create_app(storage_path)).get("/runs/boundary-demo:ambiguous-side-effect")

    assert result.final_state is JournalState.APPLIED
    assert viewer_run is not None
    assert viewer_run.state == JournalState.APPLIED
    assert viewer_run.scope is ScopeAnnotation.INSIDE_MVP_SCOPE
    assert viewer_run.boundaries == [
        BoundaryAnnotation.SIDE_EFFECTS_POSSIBLE,
        BoundaryAnnotation.TERMINAL,
    ]
    assert api_response.status_code == 200
    assert api_response.json()["scope"] == ScopeAnnotation.INSIDE_MVP_SCOPE.value
    assert api_response.json()["boundaries"] == [
        BoundaryAnnotation.SIDE_EFFECTS_POSSIBLE.value,
        BoundaryAnnotation.TERMINAL.value,
    ]


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


def test_regression_repeated_resume_reports_persisted_checkpoint_truth(tmp_path) -> None:
    storage_path = tmp_path / "journal.jsonl"
    runtime = Runtime(storage_path)
    action = ActionEnvelope(
        name="demo.resumable_boundary",
        target="local-demo",
        args={},
        diff="Boundary demo: resumable",
        actor="builder-bot",
        privileges=["demo:write"],
        idempotency_key="boundary-demo:resumable-regression",
        effect=EffectClass.REVERSIBLE_WRITE,
    )

    def pause_then_resume(checkpoint: object | None) -> dict[str, object]:
        if checkpoint is None:
            raise ResumableExecution({"step": 1})
        return {"ok": checkpoint == {"step": 1}}

    first = runtime.run(
        run_id=action.idempotency_key,
        action=action,
        executor=pause_then_resume,
    )

    runtime_view = RunViewer(runtime).get_run(action.idempotency_key)
    storage_view = RunViewer(storage_path).get_run(action.idempotency_key)

    assert first.state is JournalState.RESUMABLE
    assert runtime_view is not None and storage_view is not None
    assert runtime_view.state == storage_view.state == JournalState.RESUMABLE
    assert runtime_view.scope == storage_view.scope == ScopeAnnotation.BOUNDARY_CASE
    assert runtime_view.boundaries == storage_view.boundaries == [
        BoundaryAnnotation.CHECKPOINT_RECORDED,
        BoundaryAnnotation.SIDE_EFFECTS_POSSIBLE,
    ]
    assert runtime_view.has_checkpoint is True
    assert storage_view.has_checkpoint is True

    second = runtime.run(
        run_id=action.idempotency_key,
        action=action,
        executor=pause_then_resume,
    )
    terminal_view = RunViewer(storage_path).get_run(action.idempotency_key)

    assert second.state is JournalState.APPLIED
    assert terminal_view is not None
    assert [entry.state for entry in runtime.history(action.idempotency_key)] == [
        JournalState.PROPOSED,
        JournalState.APPROVED,
        JournalState.EXECUTING,
        JournalState.RESUMABLE,
        JournalState.EXECUTING,
        JournalState.APPLIED,
    ]
    assert terminal_view.state == JournalState.APPLIED
    assert terminal_view.scope is ScopeAnnotation.INSIDE_MVP_SCOPE
    assert terminal_view.boundaries == [
        BoundaryAnnotation.SIDE_EFFECTS_POSSIBLE,
        BoundaryAnnotation.TERMINAL,
    ]
    assert terminal_view.has_checkpoint is False


def test_regression_unsupported_rollback_expectation_stays_docs_only() -> None:
    root = Path(__file__).resolve().parents[1]
    readme = (root / "README.md").read_text()
    boundary_doc = (root / "docs/case-studies/boundary-scenarios.md").read_text()
    faq_doc = (root / "docs/faq.md").read_text()

    assert "## Compensation is not rollback" in readme
    assert "| Term | Means | Does not mean |" in readme
    assert "| Local rollback |" in readme
    assert "| Compensation |" in readme
    assert "does not mean exact rollback" in readme
    assert "## Compensation is not rollback" in faq_doc
    assert "| Term | Means | Does not mean |" in faq_doc
    assert "| Local rollback |" in faq_doc
    assert "| Compensation |" in faq_doc
    assert "Unsupported rollback expectation" in boundary_doc
    assert "Issue #12 will later" not in boundary_doc
    assert "viewer/API already expose" in boundary_doc
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
