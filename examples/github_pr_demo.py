from dataclasses import dataclass
from typing import Any, Callable

from safeloop.journal import JournalState
from safeloop.types import ActionEnvelope, EffectClass


CreatePr = Callable[..., dict[str, Any]]
ClosePr = Callable[..., None]


@dataclass(frozen=True)
class DemoResult:
    action: ActionEnvelope
    journal_states: list[JournalState]
    final_state: JournalState
    pr: dict[str, Any] | None
    compensation_triggered: bool
    error: str | None



def run_github_pr_demo(
    *,
    repo: str,
    title: str,
    body: str,
    actor: str,
    create_pr: CreatePr,
    close_pr: ClosePr,
    fail_after_create: bool = False,
) -> DemoResult:
    """Run a concrete GitHub-style create-PR flow with rollback via close-PR."""

    action = ActionEnvelope(
        name="github.create_pull_request",
        target=repo,
        args={"title": title, "body": body},
        diff=f"Create pull request on {repo}: {title}",
        actor=actor,
        privileges=["pull_requests:write"],
        idempotency_key=f"github-pr-demo:{repo}:{title}",
        effect=EffectClass.COMPENSATABLE_WRITE,
    )

    journal_states = [
        JournalState.PROPOSED,
        JournalState.APPROVED,
        JournalState.EXECUTING,
    ]

    pr = create_pr(repo=repo, title=title, body=body)

    try:
        if fail_after_create:
            raise RuntimeError("Simulated failure after pull request creation")
    except RuntimeError as exc:
        journal_states.append(JournalState.COMPENSATING)
        close_pr(repo=repo, pr_number=int(pr["number"]))
        journal_states.append(JournalState.COMPENSATED)
        return DemoResult(
            action=action,
            journal_states=journal_states,
            final_state=JournalState.COMPENSATED,
            pr=pr,
            compensation_triggered=True,
            error=str(exc),
        )

    journal_states.append(JournalState.APPLIED)
    return DemoResult(
        action=action,
        journal_states=journal_states,
        final_state=JournalState.APPLIED,
        pr=pr,
        compensation_triggered=False,
        error=None,
    )
