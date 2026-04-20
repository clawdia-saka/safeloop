"""Inspection API for persisted safeloop runs."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from safeloop.runtime import RunRecord, Runtime
from safeloop.storage import LocalJournalStorage


class RunSummary(BaseModel):
    run_id: str
    action_id: str
    state: str


class RunDetail(RunSummary):
    has_checkpoint: bool
    journal: list[dict[str, str]]


class RunViewer:
    def __init__(self, runtime: Runtime | LocalJournalStorage | str | Path) -> None:
        if isinstance(runtime, Runtime):
            self.runtime = runtime
        else:
            self.runtime = Runtime(runtime)

    def list_runs(self) -> list[RunSummary]:
        return [self._summary(record) for record in self.runtime.list_runs()]

    def get_run(self, run_id: str) -> RunDetail | None:
        record = self.runtime.get_run(run_id)
        if record is None:
            return None
        return RunDetail(
            **self._summary(record).model_dump(),
            has_checkpoint=self.runtime.checkpoint_for(run_id) is not None,
            journal=[entry.model_dump(mode="json") for entry in record.journal],
        )

    def list_journal_entries(self, run_id: str) -> list[dict[str, str]]:
        record = self.runtime.get_run(run_id)
        if record is None:
            return []
        return [entry.model_dump(mode="json") for entry in record.journal]

    @staticmethod
    def _summary(record: RunRecord) -> RunSummary:
        return RunSummary(
            run_id=record.run_id,
            action_id=record.action_id,
            state=record.state.value,
        )


def create_app(runtime: Runtime | LocalJournalStorage | str | Path) -> FastAPI:
    viewer = RunViewer(runtime)
    app = FastAPI()

    @app.get("/runs")
    def list_runs() -> list[RunSummary]:
        return viewer.list_runs()

    @app.get("/runs/{run_id}")
    def get_run(run_id: str) -> RunDetail:
        run = viewer.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return run

    @app.get("/runs/{run_id}/journal")
    def list_journal_entries(run_id: str) -> list[dict[str, str]]:
        journal = viewer.list_journal_entries(run_id)
        if not journal:
            raise HTTPException(status_code=404, detail="Run not found")
        return journal

    return app
