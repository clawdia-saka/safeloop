from pathlib import Path
import sys
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from examples.github_pr_demo import run_github_pr_demo
from safeloop.journal import JournalState
from safeloop.types import EffectClass


def test_run_github_pr_demo_applies_create_pr_without_compensation() -> None:
    create_pr = Mock(return_value={"number": 101, "url": "https://example.test/pr/101"})
    close_pr = Mock()

    result = run_github_pr_demo(
        repo="nous/safeloop",
        title="Add rollback-safe demo",
        body="This is a demo PR.",
        actor="builder-bot",
        create_pr=create_pr,
        close_pr=close_pr,
    )

    create_pr.assert_called_once_with(
        repo="nous/safeloop",
        title="Add rollback-safe demo",
        body="This is a demo PR.",
    )
    close_pr.assert_not_called()
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
        title="Add rollback-safe demo",
        body="This is a demo PR.",
        actor="builder-bot",
        create_pr=create_pr,
        close_pr=close_pr,
        fail_after_create=True,
    )

    create_pr.assert_called_once_with(
        repo="nous/safeloop",
        title="Add rollback-safe demo",
        body="This is a demo PR.",
    )
    close_pr.assert_called_once_with(repo="nous/safeloop", pr_number=101)
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
