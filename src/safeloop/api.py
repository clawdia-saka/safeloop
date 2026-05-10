"""Inspection API for persisted safeloop runs."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from safeloop.journal import JournalEntry, JournalReason, JournalState
from safeloop.local_anchor import canonical_sha256
from safeloop.runtime import RunRecord, Runtime
from safeloop.storage import LocalJournalStorage


class ScopeAnnotation(StrEnum):
    INSIDE_MVP_SCOPE = "inside_mvp_scope"
    BOUNDARY_CASE = "boundary_case"


class BoundaryAnnotation(StrEnum):
    PRE_EXECUTION = "pre_execution"
    OPERATOR_OWNED = "operator_owned"
    CHECKPOINT_RECORDED = "checkpoint_recorded"
    SIDE_EFFECTS_POSSIBLE = "side_effects_possible"
    CLEANUP_ATTEMPTED = "cleanup_attempted"
    CLEANUP_INCOMPLETE_OR_UNCERTAIN = "cleanup_incomplete_or_uncertain"
    TERMINAL = "terminal"


def derive_annotations(
    state: JournalState,
    reason: JournalReason | str | None = None,
) -> tuple[ScopeAnnotation, list[BoundaryAnnotation]]:
    normalized_reason = reason
    if isinstance(reason, str):
        try:
            normalized_reason = JournalReason(reason)
        except ValueError:
            normalized_reason = reason

    if state is JournalState.PROPOSED:
        return ScopeAnnotation.INSIDE_MVP_SCOPE, []
    if state is JournalState.APPROVED:
        return ScopeAnnotation.INSIDE_MVP_SCOPE, []
    if state is JournalState.EXECUTING:
        return ScopeAnnotation.BOUNDARY_CASE, [BoundaryAnnotation.SIDE_EFFECTS_POSSIBLE]
    if state is JournalState.APPLIED:
        return ScopeAnnotation.INSIDE_MVP_SCOPE, [BoundaryAnnotation.SIDE_EFFECTS_POSSIBLE, BoundaryAnnotation.TERMINAL]
    if state is JournalState.COMPENSATING:
        return ScopeAnnotation.BOUNDARY_CASE, [BoundaryAnnotation.CLEANUP_ATTEMPTED, BoundaryAnnotation.SIDE_EFFECTS_POSSIBLE]
    if state is JournalState.COMPENSATED:
        return ScopeAnnotation.INSIDE_MVP_SCOPE, [
            BoundaryAnnotation.CLEANUP_ATTEMPTED,
            BoundaryAnnotation.SIDE_EFFECTS_POSSIBLE,
            BoundaryAnnotation.TERMINAL,
        ]
    if state is JournalState.COMPENSATION_FAILED:
        return ScopeAnnotation.BOUNDARY_CASE, [
            BoundaryAnnotation.CLEANUP_ATTEMPTED,
            BoundaryAnnotation.CLEANUP_INCOMPLETE_OR_UNCERTAIN,
            BoundaryAnnotation.SIDE_EFFECTS_POSSIBLE,
            BoundaryAnnotation.TERMINAL,
        ]
    if state is JournalState.RESUMABLE:
        return ScopeAnnotation.BOUNDARY_CASE, [
            BoundaryAnnotation.CHECKPOINT_RECORDED,
            BoundaryAnnotation.SIDE_EFFECTS_POSSIBLE,
        ]
    if state is JournalState.HANDED_OFF:
        return ScopeAnnotation.BOUNDARY_CASE, [
            BoundaryAnnotation.PRE_EXECUTION,
            BoundaryAnnotation.OPERATOR_OWNED,
            BoundaryAnnotation.TERMINAL,
        ]
    if state is JournalState.FAILED:
        if normalized_reason in {JournalReason.APPROVAL_BLOCK, JournalReason.APPROVAL_ERROR}:
            return ScopeAnnotation.INSIDE_MVP_SCOPE, [
                BoundaryAnnotation.PRE_EXECUTION,
                BoundaryAnnotation.TERMINAL,
            ]
        if normalized_reason is JournalReason.EXECUTION_ERROR:
            return ScopeAnnotation.BOUNDARY_CASE, [
                BoundaryAnnotation.SIDE_EFFECTS_POSSIBLE,
                BoundaryAnnotation.TERMINAL,
            ]
        return ScopeAnnotation.BOUNDARY_CASE, [BoundaryAnnotation.TERMINAL]
    raise ValueError(f"Unhandled journal state for annotation derivation: {state}")


class TerminalBoundary(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    EXECUTOR_BOUNDARY = "executor_boundary"
    APPLIED = "applied"
    COMPENSATION_IN_PROGRESS = "compensation_in_progress"
    COMPENSATED = "compensated"
    COMPENSATION_FAILURE = "compensation_failure"
    CHECKPOINT_RESUME = "checkpoint_resume"
    OPERATOR_HANDOFF = "operator_handoff"
    APPROVAL_BOUNDARY = "approval_boundary"
    EXECUTION_FAILURE = "execution_failure"
    FAILED_UNKNOWN_BOUNDARY = "failed_unknown_boundary"


class TerminalSemantics(BaseModel):
    terminal_state: JournalState
    expected_terminal_state: JournalState
    boundary: TerminalBoundary
    scope_guess: ScopeAnnotation
    note: str


def derive_terminal_semantics(
    state: JournalState,
    reason: JournalReason | str | None = None,
) -> TerminalSemantics:
    normalized_reason = reason
    if isinstance(reason, str):
        try:
            normalized_reason = JournalReason(reason)
        except ValueError:
            normalized_reason = reason

    scope, _boundaries = derive_annotations(state, normalized_reason)
    boundary = TerminalBoundary.PROPOSED
    note = "Non-terminal lifecycle state."
    if state is JournalState.PROPOSED:
        boundary = TerminalBoundary.PROPOSED
        note = "Action is recorded but not yet approved or executed."
    elif state is JournalState.APPROVED:
        boundary = TerminalBoundary.APPROVED
        note = "Action is approved but has not entered the executor."
    elif state is JournalState.EXECUTING:
        boundary = TerminalBoundary.EXECUTOR_BOUNDARY
        note = "Executor is active; side effects may already be in progress."
    elif state is JournalState.APPLIED:
        boundary = TerminalBoundary.APPLIED
        note = "Terminal success; side effects may already exist."
    elif state is JournalState.COMPENSATING:
        boundary = TerminalBoundary.COMPENSATION_IN_PROGRESS
        note = "Compensation is being attempted after executor failure."
    elif state is JournalState.COMPENSATED:
        boundary = TerminalBoundary.COMPENSATED
        note = "Compensation completed, but this is mitigation rather than proof of perfect rollback."
    elif state is JournalState.COMPENSATION_FAILED:
        boundary = TerminalBoundary.COMPENSATION_FAILURE
        note = "Compensation was attempted but cleanup remains incomplete or uncertain; this is not rollback."
    elif state is JournalState.RESUMABLE:
        boundary = TerminalBoundary.CHECKPOINT_RESUME
        note = "Checkpoint exists only for the live runtime; storage-only viewers cannot resume from it."
    elif state is JournalState.HANDED_OFF:
        boundary = TerminalBoundary.OPERATOR_HANDOFF
        note = "Execution did not run; operator owns the next step."
    elif state is JournalState.FAILED:
        if normalized_reason in {JournalReason.APPROVAL_BLOCK, JournalReason.APPROVAL_ERROR}:
            boundary = TerminalBoundary.APPROVAL_BOUNDARY
            note = "Failure happened before executor entry."
        elif normalized_reason is JournalReason.EXECUTION_ERROR:
            boundary = TerminalBoundary.EXECUTION_FAILURE
            note = "Executor failed after side effects may have started."
        else:
            boundary = TerminalBoundary.FAILED_UNKNOWN_BOUNDARY
            note = "Failure reason is unknown or legacy; inspect journal details before acting."

    return TerminalSemantics(
        terminal_state=state,
        expected_terminal_state=state,
        boundary=boundary,
        scope_guess=scope,
        note=note,
    )


class ApiTraceEvidenceBinding(BaseModel):
    schema_version: str
    digest: str
    required_stages: list[str]


class JournalEntryPayload(BaseModel):
    run_id: str
    action_id: str
    state: JournalState
    reason: JournalReason | str | None = None
    error: str | None = None
    scope: ScopeAnnotation
    boundaries: list[BoundaryAnnotation]
    terminal_semantics: TerminalSemantics
    api_trace_evidence_binding: ApiTraceEvidenceBinding


class RunSummary(BaseModel):
    run_id: str
    action_id: str
    state: JournalState
    scope: ScopeAnnotation
    boundaries: list[BoundaryAnnotation]
    terminal_semantics: TerminalSemantics


class RunDetail(RunSummary):
    has_checkpoint: bool
    journal: list[JournalEntryPayload]


class RunViewer:
    def __init__(self, runtime: Runtime | LocalJournalStorage | str | Path) -> None:
        if isinstance(runtime, Runtime):
            self.runtime = runtime
        else:
            # Storage-only construction can inspect persisted journal history,
            # but it cannot recover in-memory checkpoint payloads from another
            # runtime instance. In that mode `has_checkpoint` will therefore be
            # false even if the latest persisted state is `resumable`.
            self.runtime = Runtime(runtime)

    def list_runs(self) -> list[RunSummary]:
        return [self._summary(record) for record in self.runtime.list_runs()]

    def get_run(self, run_id: str) -> RunDetail | None:
        record = self.runtime.get_run(run_id)
        if record is None:
            return None
        return RunDetail(
            **self._summary(record).model_dump(),
            has_checkpoint=record.state is JournalState.RESUMABLE and self.runtime.checkpoint_for(run_id) is not None,
            journal=[self._journal_entry_payload(entry) for entry in record.journal],
        )

    def list_journal_entries(self, run_id: str) -> list[JournalEntryPayload]:
        record = self.runtime.get_run(run_id)
        if record is None:
            return []
        return [self._journal_entry_payload(entry) for entry in record.journal]

    @staticmethod
    def _summary(record: RunRecord) -> RunSummary:
        latest_reason = record.journal[-1].reason if record.journal else None
        scope, boundaries = derive_annotations(record.state, latest_reason)
        return RunSummary(
            run_id=record.run_id,
            action_id=record.action_id,
            state=record.state,
            scope=scope,
            boundaries=boundaries,
            terminal_semantics=derive_terminal_semantics(record.state, latest_reason),
        )

    @staticmethod
    def _journal_entry_payload(entry: JournalEntry) -> JournalEntryPayload:
        scope, boundaries = derive_annotations(entry.state, entry.reason)
        terminal_semantics = derive_terminal_semantics(entry.state, entry.reason)
        return JournalEntryPayload(
            **entry.model_dump(mode="json", exclude_none=True),
            scope=scope,
            boundaries=boundaries,
            terminal_semantics=terminal_semantics,
            api_trace_evidence_binding=api_trace_evidence_binding(
                entry,
                scope=scope,
                boundaries=boundaries,
                terminal_semantics=terminal_semantics,
            ),
        )


def api_trace_evidence_binding(
    entry: JournalEntry,
    *,
    scope: ScopeAnnotation,
    boundaries: list[BoundaryAnnotation],
    terminal_semantics: TerminalSemantics,
) -> ApiTraceEvidenceBinding:
    schema_version = "api-trace-evidence-binding.v1"
    digest_payload = {
        "schema_version": schema_version,
        "run_id": entry.run_id,
        "action_id": entry.action_id,
        "state": entry.state.value,
        "reason": entry.reason.value if isinstance(entry.reason, JournalReason) else entry.reason,
        "error": entry.error,
        "scope": scope.value,
        "boundaries": [boundary.value for boundary in boundaries],
        "terminal_boundary": terminal_semantics.boundary.value,
    }
    return ApiTraceEvidenceBinding(
        schema_version=schema_version,
        digest=canonical_sha256(digest_payload),
        required_stages=["request", "runtime", "enforcement", "response"],
    )


def create_app(runtime: Runtime | LocalJournalStorage | str | Path) -> FastAPI:
    viewer = RunViewer(runtime)
    app = FastAPI()

    @app.get("/runs")
    def list_runs() -> list[RunSummary]:
        return viewer.list_runs()

    @app.get("/runs/{run_id}/journal", response_model_exclude_none=True)
    def list_journal_entries(run_id: str) -> list[JournalEntryPayload]:
        journal = viewer.list_journal_entries(run_id)
        if not journal:
            raise HTTPException(status_code=404, detail="Run not found")
        return journal

    @app.get("/runs/{run_id}", response_model_exclude_none=True)
    def get_run(run_id: str) -> RunDetail:
        run = viewer.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return run

    return app
