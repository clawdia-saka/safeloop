from __future__ import annotations

from fastapi.testclient import TestClient

from safeloop.api import RunViewer, create_app
from safeloop.journal import JournalState
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
        {"run_id": "run-1", "action_id": "action-1", "state": "applied"}
    ]


def test_get_run_returns_latest_state_and_journal(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    action = make_action(EffectClass.REVERSIBLE_WRITE)
    runtime.run(run_id="run-9", action=action, executor=lambda checkpoint: {"ok": True})

    run = RunViewer(runtime).get_run("run-9")

    assert run is not None
    assert run.model_dump() == {
        "run_id": "run-9",
        "action_id": "action-1",
        "state": "applied",
        "has_checkpoint": False,
        "journal": [
            {"run_id": "run-9", "action_id": "action-1", "state": "proposed"},
            {"run_id": "run-9", "action_id": "action-1", "state": "approved"},
            {"run_id": "run-9", "action_id": "action-1", "state": "executing"},
            {"run_id": "run-9", "action_id": "action-1", "state": "applied"},
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


def test_get_run_reports_has_checkpoint_for_runtime_backed_viewer(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    action = make_action(EffectClass.REVERSIBLE_WRITE)

    runtime.run(
        run_id="run-checkpoint",
        action=action,
        executor=lambda checkpoint: (_ for _ in ()).throw(ResumableExecution({"step": 1})),
    )

    runtime_backed = RunViewer(runtime).get_run("run-checkpoint")
    storage_backed = RunViewer(runtime.storage).get_run("run-checkpoint")

    assert runtime_backed is not None
    assert storage_backed is not None
    assert runtime_backed.model_dump()["has_checkpoint"] is True
    assert storage_backed.model_dump()["has_checkpoint"] is False
    assert runtime_backed.model_dump()["state"] == storage_backed.model_dump()["state"] == "resumable"
    assert runtime_backed.model_dump()["journal"] == storage_backed.model_dump()["journal"]
