from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from safeloop.hooks import ApprovalDecision, ApprovalHookRegistry, CompensationHookRegistry
from safeloop.journal import JournalReason, JournalState
from safeloop.runtime import ResumableExecution, Runtime
from safeloop.types import ActionEnvelope, EffectClass


@dataclass(frozen=True)
class BoundaryDemoResult:
    scenario: str
    classification: str
    action: ActionEnvelope
    journal_states: list[JournalState]
    final_state: JournalState
    final_reason: JournalReason | None
    error: str | None
    executor_called: bool
    has_checkpoint_before_resume: bool = False
    has_checkpoint_after_resume: bool = False


@dataclass(frozen=True)
class BoundaryReference:
    scenario: str
    classification: str
    summary: str
    doc_paths: list[str]


ApprovalHook = Callable[[ActionEnvelope], ApprovalDecision]


def _make_action(*, name: str, key: str, effect: EffectClass) -> ActionEnvelope:
    return ActionEnvelope(
        name=name,
        target="local-demo",
        args={},
        diff=f"Boundary demo: {name}",
        actor="builder-bot",
        privileges=["demo:write"],
        idempotency_key=key,
        effect=effect,
    )


def _runtime_for(storage_path: str | Path | None) -> tuple[Runtime, TemporaryDirectory[str] | None]:
    if storage_path is None:
        tempdir = TemporaryDirectory()
        return Runtime(Path(tempdir.name) / "journal.jsonl"), tempdir
    return Runtime(Path(storage_path)), None


def run_handoff_demo(*, storage_path: str | Path | None = None) -> BoundaryDemoResult:
    runtime, tempdir = _runtime_for(storage_path)
    action = _make_action(
        name="demo.operator_handoff",
        key="boundary-demo:handoff",
        effect=EffectClass.IRREVERSIBLE_WRITE,
    )
    approvals = ApprovalHookRegistry()
    approvals.register(lambda envelope: ApprovalDecision.ESCALATE)
    executed = False

    def executor(checkpoint: object | None) -> None:
        del checkpoint
        nonlocal executed
        executed = True

    final_entry = runtime.run(
        run_id=action.idempotency_key,
        action=action,
        executor=executor,
        approval_hooks=approvals,
    )
    journal_states = [entry.state for entry in runtime.history(action.idempotency_key)]
    result = BoundaryDemoResult(
        scenario="handoff",
        classification="boundary",
        action=action,
        journal_states=journal_states,
        final_state=final_entry.state,
        final_reason=final_entry.reason,
        error=final_entry.error,
        executor_called=executed,
    )
    if tempdir is not None:
        tempdir.cleanup()
    return result


def run_compensation_failed_demo(*, storage_path: str | Path | None = None) -> BoundaryDemoResult:
    runtime, tempdir = _runtime_for(storage_path)
    action = _make_action(
        name="demo.compensation_failure",
        key="boundary-demo:compensation-failed",
        effect=EffectClass.COMPENSATABLE_WRITE,
    )
    hooks = CompensationHookRegistry()

    def fail_cleanup(envelope: ActionEnvelope, error: Exception) -> None:
        del envelope, error
        raise RuntimeError("cleanup hook failed")

    hooks.register(fail_cleanup)
    executed = False

    def executor(checkpoint: object | None) -> None:
        del checkpoint
        nonlocal executed
        executed = True
        raise RuntimeError("apply step failed")

    final_entry = runtime.run(
        run_id=action.idempotency_key,
        action=action,
        executor=executor,
        compensation_hooks=hooks,
    )
    journal_states = [entry.state for entry in runtime.history(action.idempotency_key)]
    result = BoundaryDemoResult(
        scenario="compensation_failed",
        classification="boundary",
        action=action,
        journal_states=journal_states,
        final_state=final_entry.state,
        final_reason=final_entry.reason,
        error=final_entry.error,
        executor_called=executed,
    )
    if tempdir is not None:
        tempdir.cleanup()
    return result


def run_resumable_demo(*, storage_path: str | Path | None = None) -> BoundaryDemoResult:
    runtime, tempdir = _runtime_for(storage_path)
    action = _make_action(
        name="demo.resumable_boundary",
        key="boundary-demo:resumable",
        effect=EffectClass.REVERSIBLE_WRITE,
    )
    executed_checkpoints: list[object | None] = []

    def pause_then_resume(checkpoint: object | None) -> dict[str, object]:
        executed_checkpoints.append(checkpoint)
        if checkpoint is None:
            raise ResumableExecution({"step": 1})
        return {"ok": checkpoint == {"step": 1}}

    first_entry = runtime.run(
        run_id=action.idempotency_key,
        action=action,
        executor=pause_then_resume,
    )
    has_checkpoint_before_resume = runtime.checkpoint_for(action.idempotency_key) is not None
    final_entry = runtime.run(
        run_id=action.idempotency_key,
        action=action,
        executor=pause_then_resume,
    )
    journal_states = [entry.state for entry in runtime.history(action.idempotency_key)]
    result = BoundaryDemoResult(
        scenario="resumable",
        classification="boundary",
        action=action,
        journal_states=journal_states,
        final_state=final_entry.state,
        final_reason=final_entry.reason,
        error=first_entry.error or final_entry.error,
        executor_called=bool(executed_checkpoints),
        has_checkpoint_before_resume=has_checkpoint_before_resume,
        has_checkpoint_after_resume=runtime.checkpoint_for(action.idempotency_key) is not None,
    )
    if tempdir is not None:
        tempdir.cleanup()
    return result


def run_repeated_resume_demo(*, storage_path: str | Path | None = None) -> BoundaryDemoResult:
    runtime, tempdir = _runtime_for(storage_path)
    action = _make_action(
        name="demo.repeated_resume_boundary",
        key="boundary-demo:repeated-resume",
        effect=EffectClass.REVERSIBLE_WRITE,
    )
    executed_checkpoints: list[object | None] = []

    def pause_twice_then_apply(checkpoint: object | None) -> dict[str, object]:
        executed_checkpoints.append(checkpoint)
        if checkpoint is None:
            raise ResumableExecution({"step": 1})
        if checkpoint == {"step": 1}:
            raise ResumableExecution({"step": 2})
        return {"ok": checkpoint == {"step": 2}}

    first_entry = runtime.run(
        run_id=action.idempotency_key,
        action=action,
        executor=pause_twice_then_apply,
    )
    second_entry = runtime.run(
        run_id=action.idempotency_key,
        action=action,
        executor=pause_twice_then_apply,
    )
    has_checkpoint_before_resume = runtime.checkpoint_for(action.idempotency_key) is not None
    final_entry = runtime.run(
        run_id=action.idempotency_key,
        action=action,
        executor=pause_twice_then_apply,
    )
    journal_states = [entry.state for entry in runtime.history(action.idempotency_key)]
    result = BoundaryDemoResult(
        scenario="repeated_resume",
        classification="boundary",
        action=action,
        journal_states=journal_states,
        final_state=final_entry.state,
        final_reason=final_entry.reason,
        error=first_entry.error or second_entry.error or final_entry.error,
        executor_called=bool(executed_checkpoints),
        has_checkpoint_before_resume=has_checkpoint_before_resume,
        has_checkpoint_after_resume=runtime.checkpoint_for(action.idempotency_key) is not None,
    )
    if tempdir is not None:
        tempdir.cleanup()
    return result


def describe_unsupported_rollback_expectation() -> BoundaryReference:
    return BoundaryReference(
        scenario="unsupported_rollback_expectation",
        classification="unsupported",
        summary=(
            "Compensation should not be misread as perfect rollback or "
            "as-if-never-happened recovery."
        ),
        doc_paths=[
            "docs/case-studies/boundary-scenarios.md",
            "docs/faq.md",
            "docs/case-studies/github-pr-demo.md",
        ],
    )


if __name__ == "__main__":
    for result in (
        run_handoff_demo(),
        run_compensation_failed_demo(),
        run_resumable_demo(),
        run_repeated_resume_demo(),
    ):
        print(
            result.scenario,
            result.classification,
            result.final_state.value,
            [state.value for state in result.journal_states],
        )
