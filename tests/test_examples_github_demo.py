from pathlib import Path
import sys
from unittest.mock import Mock

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from examples.github_pr_demo import run_github_pr_demo
from safeloop.api import RunViewer, create_app
from safeloop.journal import JournalState
from safeloop.types import EffectClass


def test_run_github_pr_demo_applies_create_pr_without_compensation() -> None:
    create_pr = Mock(return_value={"number": 101, "url": "https://example.test/pr/101"})
    close_pr = Mock()

    result = run_github_pr_demo(
        repo="nous/safeloop",
        title="Add compensation-safe demo",
        body="This is a demo PR.",
        actor="builder-bot",
        create_pr=create_pr,
        close_pr=close_pr,
    )

    create_pr.assert_called_once_with(
        repo="nous/safeloop",
        title="Add compensation-safe demo",
        body="This is a demo PR.",
    )
    close_pr.assert_not_called()
    assert result.classification == "in_scope"
    assert result.final_state is JournalState.APPLIED
    assert result.compensation_triggered is False
    assert result.pr == {"number": 101, "url": "https://example.test/pr/101"}
    assert result.action.name == "github.create_pull_request"
    assert result.action.effect is EffectClass.COMPENSATABLE_WRITE
    assert result.journal_states == [
        JournalState.PROPOSED,
        JournalState.APPROVED,
        JournalState.EXECUTING,
        JournalState.APPLIED,
    ]



def test_run_github_pr_demo_compensates_when_follow_up_step_fails() -> None:
    create_pr = Mock(return_value={"number": 101, "url": "https://example.test/pr/101"})
    close_pr = Mock()

    result = run_github_pr_demo(
        repo="nous/safeloop",
        title="Add compensation-safe demo",
        body="This is a demo PR.",
        actor="builder-bot",
        create_pr=create_pr,
        close_pr=close_pr,
        fail_after_create=True,
    )

    create_pr.assert_called_once_with(
        repo="nous/safeloop",
        title="Add compensation-safe demo",
        body="This is a demo PR.",
    )
    close_pr.assert_called_once_with(repo="nous/safeloop", pr_number=101)
    assert result.classification == "in_scope"
    assert result.final_state is JournalState.COMPENSATED
    assert result.compensation_triggered is True
    assert result.error == "Simulated failure after pull request creation"
    assert result.journal_states == [
        JournalState.PROPOSED,
        JournalState.APPROVED,
        JournalState.EXECUTING,
        JournalState.COMPENSATING,
        JournalState.COMPENSATED,
    ]


def test_run_github_pr_demo_persists_runtime_truth_for_viewer_and_api(tmp_path) -> None:
    storage_path = tmp_path / "journal.jsonl"
    create_pr = Mock(return_value={"number": 101, "url": "https://example.test/pr/101"})
    close_pr = Mock()
    repo = "nous/safeloop"

    result = run_github_pr_demo(
        repo=repo,
        title="Add compensation-safe demo",
        body="This is a demo PR.",
        actor="builder-bot",
        create_pr=create_pr,
        close_pr=close_pr,
        storage_path=storage_path,
    )

    run_id = result.action.idempotency_key
    viewer_run = RunViewer(storage_path).get_run(run_id)
    api_response = TestClient(create_app(storage_path)).get(f"/runs/{run_id}")

    assert viewer_run is not None
    assert viewer_run.state == JournalState.APPLIED.value
    assert [entry.state for entry in viewer_run.journal] == [
        JournalState.PROPOSED.value,
        JournalState.APPROVED.value,
        JournalState.EXECUTING.value,
        JournalState.APPLIED.value,
    ]
    assert api_response.status_code == 200
    assert api_response.json() == viewer_run.model_dump(mode="json", exclude_none=True)
    assert [state.value for state in result.journal_states] == [entry.state for entry in viewer_run.journal]


def test_run_github_pr_demo_persists_compensation_path_for_viewer_and_api(tmp_path) -> None:
    storage_path = tmp_path / "journal.jsonl"
    create_pr = Mock(return_value={"number": 101, "url": "https://example.test/pr/101"})
    close_pr = Mock()
    repo = "nous/safeloop"

    result = run_github_pr_demo(
        repo=repo,
        title="Add compensation-safe demo",
        body="This is a demo PR.",
        actor="builder-bot",
        create_pr=create_pr,
        close_pr=close_pr,
        fail_after_create=True,
        storage_path=storage_path,
    )

    run_id = result.action.idempotency_key
    viewer_run = RunViewer(storage_path).get_run(run_id)
    api_response = TestClient(create_app(storage_path)).get(f"/runs/{run_id}")

    assert viewer_run is not None
    assert result.final_state is JournalState.COMPENSATED
    assert viewer_run.state == JournalState.COMPENSATED.value
    assert viewer_run.journal[-2].state == JournalState.COMPENSATING.value
    assert viewer_run.journal[-2].error == "Simulated failure after pull request creation"
    assert viewer_run.journal[-1].state == JournalState.COMPENSATED.value
    assert api_response.status_code == 200
    assert api_response.json() == viewer_run.model_dump(mode="json", exclude_none=True)
