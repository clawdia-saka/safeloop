from __future__ import annotations

from fastapi.testclient import TestClient

from safeloop.api import RunViewer, create_app, derive_annotations, derive_terminal_semantics
from safeloop.hooks import ApprovalDecision, ApprovalHookRegistry, CompensationHookRegistry
from safeloop.journal import JournalReason, JournalState
from safeloop.local_anchor import canonical_sha256
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


def without_terminal_semantics(value):
    if isinstance(value, list):
        return [without_terminal_semantics(item) for item in value]
    if isinstance(value, dict):
        return {
            key: without_terminal_semantics(item)
            for key, item in value.items()
            if key not in {"terminal_semantics", "api_trace_evidence_binding"}
        }
    return value


def test_viewer_lists_storage_backed_runs(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    action = make_action(EffectClass.REVERSIBLE_WRITE)
    runtime.run(run_id="run-1", action=action, executor=lambda checkpoint: {"ok": True})

    viewer = RunViewer(runtime)

    assert without_terminal_semantics([run.model_dump(mode="json") for run in viewer.list_runs()]) == [
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
    assert without_terminal_semantics(run.model_dump(mode="json", exclude_none=True)) == {
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
    assert without_terminal_semantics(journal.json()[-1]) == {
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
    assert without_terminal_semantics(journal.json()[-2]) == {
        "run_id": "run-comp-failed",
        "action_id": "action-comp-failed",
        "state": "compensating",
        "reason": JournalReason.EXECUTION_ERROR.value,
        "error": "boom",
        "scope": "boundary_case",
        "boundaries": ["cleanup_attempted", "side_effects_possible"],
    }
    assert without_terminal_semantics(journal.json()[-1]) == {
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
    assert without_terminal_semantics(journal.json()[-1]) == {
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
    assert without_terminal_semantics(journal.json()[-1]) == {
        "run_id": "run-execution-error",
        "action_id": "action-execution-error",
        "state": "failed",
        "reason": JournalReason.EXECUTION_ERROR.value,
        "error": "executor boom",
        "scope": "boundary_case",
        "boundaries": ["side_effects_possible", "terminal"],
    }


def test_http_api_list_surfaces_terminal_semantics_for_boundary_states(tmp_path) -> None:
    storage_path = tmp_path / "journal.jsonl"
    runtime = Runtime(storage_path)

    approvals = ApprovalHookRegistry()
    approvals.register(lambda envelope: ApprovalDecision.ESCALATE)
    runtime.run(
        run_id="run-handoff-list",
        action=make_action(EffectClass.IRREVERSIBLE_WRITE, key="list-handoff"),
        executor=lambda checkpoint: (_ for _ in ()).throw(RuntimeError("must not execute")),
        approval_hooks=approvals,
    )

    hooks = CompensationHookRegistry()
    hooks.register(lambda envelope, error: (_ for _ in ()).throw(RuntimeError("comp failed")))
    runtime.run(
        run_id="run-comp-failed-list",
        action=make_action(EffectClass.COMPENSATABLE_WRITE, key="list-comp-failed"),
        executor=lambda checkpoint: (_ for _ in ()).throw(RuntimeError("boom")),
        compensation_hooks=hooks,
    )

    response = TestClient(create_app(storage_path)).get("/runs")

    assert response.status_code == 200
    by_id = {item["run_id"]: item for item in response.json()}
    assert by_id["run-handoff-list"]["terminal_semantics"] == {
        "terminal_state": "handed_off",
        "expected_terminal_state": "handed_off",
        "boundary": "operator_handoff",
        "scope_guess": "boundary_case",
        "note": "Execution did not run; operator owns the next step.",
    }
    assert by_id["run-comp-failed-list"]["terminal_semantics"] == {
        "terminal_state": "compensation_failed",
        "expected_terminal_state": "compensation_failed",
        "boundary": "compensation_failure",
        "scope_guess": "boundary_case",
        "note": "Compensation was attempted but cleanup remains incomplete or uncertain; this is not rollback.",
    }


def test_http_api_journal_surfaces_resumable_terminal_semantics(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    action = make_action(EffectClass.REVERSIBLE_WRITE, key="journal-semantics-resume")

    runtime.run(
        run_id="run-journal-semantics-resume",
        action=action,
        executor=lambda checkpoint: (_ for _ in ()).throw(ResumableExecution({"step": 1})),
    )
    runtime.run(
        run_id="run-journal-semantics-resume",
        action=action,
        executor=lambda checkpoint: {"ok": checkpoint == {"step": 1}},
    )

    response = TestClient(create_app(runtime)).get("/runs/run-journal-semantics-resume/journal")

    assert response.status_code == 200
    states = {entry["state"]: entry for entry in response.json()}
    assert states["resumable"]["terminal_semantics"] == {
        "terminal_state": "resumable",
        "expected_terminal_state": "resumable",
        "boundary": "checkpoint_resume",
        "scope_guess": "boundary_case",
        "note": "Checkpoint exists only for the live runtime; storage-only viewers cannot resume from it.",
    }
    assert states["applied"]["terminal_semantics"] == {
        "terminal_state": "applied",
        "expected_terminal_state": "applied",
        "boundary": "applied",
        "scope_guess": "inside_mvp_scope",
        "note": "Terminal success; side effects may already exist.",
    }


def test_http_api_binds_journal_entries_to_digest_bound_trace_evidence(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    action = make_action(EffectClass.REVERSIBLE_WRITE, key="trace-binding")
    runtime.run(run_id="run-trace-binding", action=action, executor=lambda checkpoint: {"ok": True})

    response = TestClient(create_app(runtime)).get("/runs/run-trace-binding/journal")

    assert response.status_code == 200
    entries = response.json()
    first = entries[0]
    expected_stage_digests = {
        "request": canonical_sha256({"method": "GET", "path_template": "/runs/{run_id}/journal", "run_id": "run-trace-binding"}),
        "runtime": canonical_sha256({"run_id": "run-trace-binding", "action_id": "trace-binding", "state": "proposed", "reason": None, "error": None}),
        "enforcement": canonical_sha256({"scope": "inside_mvp_scope", "boundaries": [], "terminal_boundary": "proposed"}),
        "response": canonical_sha256({"schema_version": "api-trace-evidence-binding.v1", "run_id": "run-trace-binding", "action_id": "trace-binding", "state": "proposed"}),
    }
    assert first["api_trace_evidence_binding"] == {
        "schema_version": "api-trace-evidence-binding.v1",
        "digest": canonical_sha256({"schema_version": "api-trace-evidence-binding.v1", "stage_digests": expected_stage_digests}),
        "required_stages": ["request", "runtime", "enforcement", "response"],
        "stage_digests": expected_stage_digests,
    }
    for entry in entries:
        binding = entry["api_trace_evidence_binding"]
        assert binding["schema_version"] == "api-trace-evidence-binding.v1"
        assert binding["digest"].startswith("sha256:")
        assert binding["required_stages"] == ["request", "runtime", "enforcement", "response"]
        assert set(binding["stage_digests"]) == set(binding["required_stages"])


def test_failed_approval_completion_error_is_side_effects_possible_boundary() -> None:
    scope, boundaries = derive_annotations(JournalState.FAILED, JournalReason.APPROVAL_COMPLETION_ERROR)

    assert scope.value == "boundary_case"
    assert [boundary.value for boundary in boundaries] == ["side_effects_possible", "terminal"]


def test_failed_resume_approval_block_is_side_effects_possible_boundary() -> None:
    scope, boundaries = derive_annotations(JournalState.FAILED, JournalReason.RESUME_APPROVAL_BLOCK)
    semantics = derive_terminal_semantics(JournalState.FAILED, JournalReason.RESUME_APPROVAL_BLOCK)

    assert scope.value == "boundary_case"
    assert [boundary.value for boundary in boundaries] == [
        "checkpoint_recorded",
        "side_effects_possible",
        "terminal",
    ]
    assert semantics.boundary.value == "checkpoint_resume"
    assert "prior side effects may exist" in semantics.note


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
