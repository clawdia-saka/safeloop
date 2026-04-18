from fastapi.testclient import TestClient

from safeloop.api import create_app
from safeloop.journal import JournalEntry, JournalState


def test_list_runs_returns_latest_state_per_run() -> None:
    app = create_app(
        [
            JournalEntry(run_id="run-1", action_id="action-1", state=JournalState.PROPOSED),
            JournalEntry(run_id="run-1", action_id="action-1", state=JournalState.APPROVED),
            JournalEntry(run_id="run-1", action_id="action-1", state=JournalState.APPLIED),
            JournalEntry(run_id="run-2", action_id="action-9", state=JournalState.RESUMABLE),
        ]
    )

    response = TestClient(app).get("/runs")

    assert response.status_code == 200
    assert response.json() == [
        {"run_id": "run-1", "action_id": "action-1", "state": "applied"},
        {"run_id": "run-2", "action_id": "action-9", "state": "resumable"},
    ]


def test_get_run_details_returns_latest_state_and_history() -> None:
    app = create_app(
        [
            JournalEntry(run_id="run-9", action_id="action-3", state=JournalState.PROPOSED),
            JournalEntry(run_id="run-9", action_id="action-3", state=JournalState.APPROVED),
            JournalEntry(run_id="run-9", action_id="action-3", state=JournalState.RESUMABLE),
        ]
    )

    response = TestClient(app).get("/runs/run-9")

    assert response.status_code == 200
    assert response.json() == {
        "run_id": "run-9",
        "action_id": "action-3",
        "state": "resumable",
        "journal": [
            {"run_id": "run-9", "action_id": "action-3", "state": "proposed"},
            {"run_id": "run-9", "action_id": "action-3", "state": "approved"},
            {"run_id": "run-9", "action_id": "action-3", "state": "resumable"},
        ],
    }



def test_list_journal_entries_returns_ordered_entries_for_run() -> None:
    app = create_app(
        [
            JournalEntry(run_id="run-7", action_id="action-2", state=JournalState.PROPOSED),
            JournalEntry(run_id="run-7", action_id="action-2", state=JournalState.APPROVED),
            JournalEntry(run_id="run-7", action_id="action-2", state=JournalState.EXECUTING),
            JournalEntry(run_id="run-7", action_id="action-2", state=JournalState.COMPENSATING),
            JournalEntry(run_id="run-7", action_id="action-2", state=JournalState.COMPENSATED),
        ]
    )

    response = TestClient(app).get("/runs/run-7/journal")

    assert response.status_code == 200
    assert response.json() == [
        {"run_id": "run-7", "action_id": "action-2", "state": "proposed"},
        {"run_id": "run-7", "action_id": "action-2", "state": "approved"},
        {"run_id": "run-7", "action_id": "action-2", "state": "executing"},
        {"run_id": "run-7", "action_id": "action-2", "state": "compensating"},
        {"run_id": "run-7", "action_id": "action-2", "state": "compensated"},
    ]
