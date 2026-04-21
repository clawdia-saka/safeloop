from __future__ import annotations

from fastapi.testclient import TestClient

from safeloop.api import RunViewer, create_app, derive_annotations
from safeloop.hooks import ApprovalDecision, ApprovalHookRegistry, CompensationHookRegistry
from safeloop.journal import JournalReason, JournalState
from safeloop.runtime import ResumableExecution, Runtime
from safeloop.types import ActionEnvelope, EffectClass


def make_action(effect: EffectClass, key: str = "action-1") -> ActionEnvelope:
    return ActionEnvelope(
        name="demo",
        target="system",
        args={},
        diff="",
        actor="tester",
        privileges=[],
        idempotency_key=key,
        effect=effect,
    )


def make_runtime(tmp_path) -> Runtime:
    return Runtime(tmp_path / "journal.jsonl")


def test_viewer_lists_storage_backed_runs(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    action = make_action(EffectClass.REVERSIBLE_WRITE)
    runtime.run(run_id="run-1", action=action, executor=lambda checkpoint: {"ok": True})

    viewer = RunViewer(runtime)

    assert [run.model_dump() for run in viewer.list_runs()] == [
        {
            "run_id": "run-1",
            "action_id": "action-1",
            "state": "applied",
            "scope": "inside_mvp_scope",
            "boundaries": ["side_effects_possible", "terminal"],
        }
    ]


def test_get_run_returns_latest_state_and_journal(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    action = make_action(EffectClass.REVERSIBLE_WRITE)
    runtime.run(run_id="run-9", action=action, executor=lambda checkpoint: {"ok": True})

    run = RunViewer(runtime).get_run("run-9")

    assert run is not None
    assert run.model_dump(exclude_none=True) == {
        "run_id": "run-9",
        "action_id": "action-1",
        "state": "applied",
        "scope": "inside_mvp_scope",
        "boundaries": ["side_effects_possible", "terminal"],
        "has_checkpoint": False,
        "journal": [
            {
                "run_id": "run-9",
                "action_id": "action-1",
                "state": "proposed",
                "scope": "inside_mvp_scope",
                "boundaries": [],
            },
            {
                "run_id": "run-9",
                "action_id": "action-1",
                "state": "approved",
                "scope": "inside_mvp_scope",
                "boundaries": [],
            },
            {
                "run_id": "run-9",
                "action_id": "action-1",
                "state": "executing",
                "scope": "boundary_case",
                "boundaries": ["side_effects_possible"],
            },
            {
                "run_id": "run-9",
                "action_id": "action-1",
                "state": "applied",
                "scope": "inside_mvp_scope",
                "boundaries": ["side_effects_possible", "terminal"],
            },
        ],
    }


def test_http_api_returns_404_for_unknown_run(tmp_path) -> None:
    client = TestClient(create_app(make_runtime(tmp_path)))

    response = client.get("/runs/missing-run")

    assert response.status_code == 404
    assert response.json() == {"detail": "Run not found"}


def test_http_api_lists_journal_entries_in_append_order(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    action = make_action(EffectClass.REVERSIBLE_WRITE)
    runtime.run(run_id="run-2", action=action, executor=lambda checkpoint: {"ok": True})

    response = TestClient(create_app(runtime)).get("/runs/run-2/journal")

    assert response.status_code == 200
    assert [entry["state"] for entry in response.json()] == [
        JournalState.PROPOSED.value,
        JournalState.APPROVED.value,
        JournalState.EXECUTING.value,
        JournalState.APPLIED.value,
    ]


def test_http_api_exposes_handoff_state_and_reason(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    approvals = ApprovalHookRegistry()
    approvals.register(lambda envelope: ApprovalDecision.ESCALATE)
    action = make_action(EffectClass.IRREVERSIBLE_WRITE, key="action-handoff")
    runtime.run(
        run_id="run-handoff-api",
        action=action,
        executor=lambda checkpoint: (_ for _ in ()).throw(RuntimeError("should not execute")),
        approval_hooks=approvals,
    )

    client = TestClient(create_app(runtime))
    detail = client.get("/runs/run-handoff-api")
    journal = client.get("/runs/run-handoff-api/journal")

    assert detail.status_code == 200
    assert detail.json()["state"] == "handed_off"
    assert detail.json()["scope"] == "boundary_case"
    assert detail.json()["boundaries"] == ["pre_execution", "operator_owned", "terminal"]
    assert journal.status_code == 200
    assert journal.json()[-1] == {
        "run_id": "run-handoff-api",
        "action_id": "action-handoff",
        "state": "handed_off",
        "reason": "handoff_requested",
        "scope": "boundary_case",
        "boundaries": ["pre_execution", "operator_owned", "terminal"],
    }


def test_http_api_exposes_has_checkpoint_for_resumable_runs(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    action = make_action(EffectClass.REVERSIBLE_WRITE, key="api-resume")
    runtime.run(
        run_id="run-api-resume",
        action=action,
        executor=lambda checkpoint: (_ for _ in ()).throw(ResumableExecution({"step": 1})),
    )

    client = TestClient(create_app(runtime))
    detail = client.get("/runs/run-api-resume")

    assert detail.status_code == 200
    assert detail.json()["state"] == "resumable"
    assert detail.json()["scope"] == "boundary_case"
    assert detail.json()["boundaries"] == ["checkpoint_recorded", "side_effects_possible"]
    assert detail.json()["has_checkpoint"] is True


def test_storage_backed_viewer_reports_no_live_checkpoint_for_resumable_run(tmp_path) -> None:
    storage_path = tmp_path / "journal.jsonl"
    runtime = Runtime(storage_path)
    action = make_action(EffectClass.REVERSIBLE_WRITE, key="viewer-resume")
    runtime.run(
        run_id="run-viewer-resume",
        action=action,
        executor=lambda checkpoint: (_ for _ in ()).throw(ResumableExecution({"step": 1})),
    )

    run = RunViewer(storage_path).get_run("run-viewer-resume")

    assert run is not None
    assert run.state == JournalState.RESUMABLE
    assert run.scope.value == "boundary_case"
    assert [boundary.value for boundary in run.boundaries] == ["checkpoint_recorded", "side_effects_possible"]
    assert run.has_checkpoint is False


def test_http_api_clears_has_checkpoint_after_terminal_resume(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    action = make_action(EffectClass.REVERSIBLE_WRITE, key="api-resume-terminal")

    runtime.run(
        run_id="run-api-resume-terminal",
        action=action,
        executor=lambda checkpoint: (_ for _ in ()).throw(ResumableExecution({"step": 1})),
    )
    runtime.run(
        run_id="run-api-resume-terminal",
        action=action,
        executor=lambda checkpoint: {"ok": checkpoint == {"step": 1}},
    )

    client = TestClient(create_app(runtime))
    detail = client.get("/runs/run-api-resume-terminal")

    assert detail.status_code == 200
    assert detail.json()["state"] == "applied"
    assert detail.json()["has_checkpoint"] is False


def test_http_api_exposes_compensation_failed_state_and_metadata(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    hooks = CompensationHookRegistry()
    hooks.register(lambda envelope, error: (_ for _ in ()).throw(RuntimeError("comp failed")))
    action = make_action(EffectClass.COMPENSATABLE_WRITE, key="action-comp-failed")
    runtime.run(
        run_id="run-comp-failed",
        action=action,
        executor=lambda checkpoint: (_ for _ in ()).throw(RuntimeError("boom")),
        compensation_hooks=hooks,
    )

    client = TestClient(create_app(runtime))
    detail = client.get("/runs/run-comp-failed")
    journal = client.get("/runs/run-comp-failed/journal")

    assert detail.status_code == 200
    assert detail.json()["state"] == "compensation_failed"
    assert detail.json()["scope"] == "boundary_case"
    assert detail.json()["boundaries"] == [
        "cleanup_attempted",
        "cleanup_incomplete_or_uncertain",
        "side_effects_possible",
        "terminal",
    ]
    assert journal.status_code == 200
    assert journal.json()[-2] == {
        "run_id": "run-comp-failed",
        "action_id": "action-comp-failed",
        "state": "compensating",
        "reason": JournalReason.EXECUTION_ERROR.value,
        "error": "boom",
        "scope": "boundary_case",
        "boundaries": ["cleanup_attempted", "side_effects_possible"],
    }
    assert journal.json()[-1] == {
        "run_id": "run-comp-failed",
        "action_id": "action-comp-failed",
        "state": "compensation_failed",
        "reason": JournalReason.COMPENSATION_ERROR.value,
        "error": "comp failed",
        "scope": "boundary_case",
        "boundaries": [
            "cleanup_attempted",
            "cleanup_incomplete_or_uncertain",
            "side_effects_possible",
            "terminal",
        ],
    }


def test_http_api_exposes_approval_error_metadata(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    approvals = ApprovalHookRegistry()
    approvals.register(lambda envelope: (_ for _ in ()).throw(RuntimeError("approval failed")))
    action = make_action(EffectClass.REVERSIBLE_WRITE, key="action-approval-error")
    runtime.run(
        run_id="run-approval-error",
        action=action,
        executor=lambda checkpoint: {"ok": True},
        approval_hooks=approvals,
    )

    client = TestClient(create_app(runtime))
    detail = client.get("/runs/run-approval-error")
    journal = client.get("/runs/run-approval-error/journal")

    assert detail.status_code == 200
    assert detail.json()["state"] == "failed"
    assert detail.json()["scope"] == "inside_mvp_scope"
    assert detail.json()["boundaries"] == ["pre_execution", "terminal"]
    assert journal.status_code == 200
    assert journal.json()[-1] == {
        "run_id": "run-approval-error",
        "action_id": "action-approval-error",
        "state": "failed",
        "reason": JournalReason.APPROVAL_ERROR.value,
        "error": "approval failed",
        "scope": "inside_mvp_scope",
        "boundaries": ["pre_execution", "terminal"],
    }


def test_http_api_exposes_execution_error_scope_and_boundaries(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    action = make_action(EffectClass.REVERSIBLE_WRITE, key="action-execution-error")
    runtime.run(
        run_id="run-execution-error",
        action=action,
        executor=lambda checkpoint: (_ for _ in ()).throw(RuntimeError("executor boom")),
    )

    client = TestClient(create_app(runtime))
    detail = client.get("/runs/run-execution-error")
    journal = client.get("/runs/run-execution-error/journal")

    assert detail.status_code == 200
    assert detail.json()["state"] == "failed"
    assert detail.json()["scope"] == "boundary_case"
    assert detail.json()["boundaries"] == ["side_effects_possible", "terminal"]
    assert journal.status_code == 200
    assert journal.json()[-1] == {
        "run_id": "run-execution-error",
        "action_id": "action-execution-error",
        "state": "failed",
        "reason": JournalReason.EXECUTION_ERROR.value,
        "error": "executor boom",
        "scope": "boundary_case",
        "boundaries": ["side_effects_possible", "terminal"],
    }


def test_failed_unknown_reason_defaults_to_boundary_terminal_only() -> None:
    scope, boundaries = derive_annotations(JournalState.FAILED, None)

    assert scope.value == "boundary_case"
    assert [boundary.value for boundary in boundaries] == ["terminal"]


def test_viewer_tolerates_failed_legacy_unknown_reason(tmp_path) -> None:
    storage_path = tmp_path / "journal.jsonl"
    storage_path.write_text(
        "\n".join(
            [
                '{"run_id":"legacy-run","action_id":"legacy-action","state":"proposed"}',
                '{"run_id":"legacy-run","action_id":"legacy-action","state":"failed","reason":"legacy_unknown_reason","error":"legacy boom"}',
            ]
        )
        + "\n"
    )

    run = RunViewer(storage_path).get_run("legacy-run")
    journal = RunViewer(storage_path).list_journal_entries("legacy-run")

    assert run is not None
    assert run.state == JournalState.FAILED
    assert run.scope.value == "boundary_case"
    assert [boundary.value for boundary in run.boundaries] == ["terminal"]
    assert journal[-1].reason == "legacy_unknown_reason"
    assert journal[-1].scope.value == "boundary_case"
    assert [boundary.value for boundary in journal[-1].boundaries] == ["terminal"]
