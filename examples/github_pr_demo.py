from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from safeloop.hooks import CompensationHookRegistry
from safeloop.journal import JournalState
from safeloop.runtime import Runtime
from safeloop.types import ActionEnvelope, EffectClass


CreatePr = Callable[..., dict[str, Any]]
ClosePr = Callable[..., None]


@dataclass(frozen=True)
class DemoResult:
    classification: str
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
    storage_path: str | Path | None = None,
) -> DemoResult:
    """Run a concrete GitHub-style create-PR flow with compensation via close-PR."""

    safe_repo = repo.replace("/", "--")
    action = ActionEnvelope(
        name="github.create_pull_request",
        target=repo,
        args={"title": title, "body": body},
        diff=f"Create pull request on {repo}: {title}",
        actor=actor,
        privileges=["pull_requests:write"],
        idempotency_key=f"github-pr-demo:{safe_repo}:{title}",
        effect=EffectClass.COMPENSATABLE_WRITE,
    )
    run_id = action.idempotency_key

    tempdir: TemporaryDirectory[str] | None = None
    if storage_path is None:
        tempdir = TemporaryDirectory()
        storage = Path(tempdir.name) / "journal.jsonl"
    else:
        storage = Path(storage_path)

    runtime = Runtime(storage)
    compensation_hooks = CompensationHookRegistry()
    pr: dict[str, Any] | None = None
    error: str | None = None

    def compensate(envelope: ActionEnvelope, exc: Exception) -> None:
        del envelope
        nonlocal pr, error
        error = str(exc)
        if pr is None:
            return
        close_pr(repo=repo, pr_number=int(pr["number"]))

    compensation_hooks.register(compensate)

    def executor(checkpoint: object | None) -> dict[str, Any]:
        del checkpoint
        nonlocal pr
        pr = create_pr(repo=repo, title=title, body=body)
        if fail_after_create:
            raise RuntimeError("Simulated failure after pull request creation")
        return pr

    try:
        final_entry = runtime.run(
            run_id=run_id,
            action=action,
            executor=executor,
            compensation_hooks=compensation_hooks,
        )
        journal_states = [entry.state for entry in runtime.history(run_id)]

        return DemoResult(
            classification="in_scope",
            action=action,
            journal_states=journal_states,
            final_state=final_entry.state,
            pr=pr,
            compensation_triggered=final_entry.state is JournalState.COMPENSATED,
            error=error,
        )
    finally:
        if tempdir is not None:
            tempdir.cleanup()


if __name__ == "__main__":
    def fake_create_pr(*, repo: str, title: str, body: str) -> dict[str, object]:
        return {"number": 101, "url": f"https://example.test/{repo}/pull/101"}

    def fake_close_pr(*, repo: str, pr_number: int) -> None:
        print(f"Closed PR #{pr_number} for {repo}")

    result = run_github_pr_demo(
        repo="nous/safeloop",
        title="Add compensation-safe demo",
        body="This is a demo PR.",
        actor="builder-bot",
        create_pr=fake_create_pr,
        close_pr=fake_close_pr,
        fail_after_create=True,
    )

    print(result.final_state.value)
    print(result.compensation_triggered)
