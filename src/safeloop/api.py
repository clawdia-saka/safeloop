"""FastAPI inspection API for persisted safeloop runs."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException

from safeloop.runtime import Runtime
from safeloop.storage import LocalJournalStorage
from safeloop.viewer import (
    BoundaryAnnotation,
    JournalEntryPayload,
    RunDetail,
    RunSummary,
    RunViewer,
    ScopeAnnotation,
    TerminalBoundary,
    TerminalSemantics,
    api_trace_evidence_binding,
    derive_annotations,
    derive_terminal_semantics,
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
