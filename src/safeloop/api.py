"""Minimal local API for viewing run and journal state."""

from collections.abc import Iterable

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from safeloop.journal import JournalEntry


class RunSummary(BaseModel):
    run_id: str
    action_id: str
    state: str


class RunDetail(RunSummary):
    journal: list[JournalEntry]


class RunViewer:
    def __init__(self, entries: Iterable[JournalEntry] | None = None) -> None:
        self._entries = list(entries or [])

    def list_runs(self) -> list[RunSummary]:
        latest_by_run: dict[str, JournalEntry] = {}
        for entry in reversed(self._entries):
            latest_by_run.setdefault(entry.run_id, entry)

        return [
            RunSummary(
                run_id=entry.run_id,
                action_id=entry.action_id,
                state=entry.state.value,
            )
            for _, entry in sorted(latest_by_run.items())
        ]

    def get_run(self, run_id: str) -> RunDetail | None:
        journal = self.list_journal_entries(run_id)
        if not journal:
            return None

        latest = journal[-1]
        return RunDetail(
            run_id=latest.run_id,
            action_id=latest.action_id,
            state=latest.state.value,
            journal=journal,
        )

    def list_journal_entries(self, run_id: str) -> list[JournalEntry]:
        return [entry for entry in self._entries if entry.run_id == run_id]


def create_app(entries: Iterable[JournalEntry] | None = None) -> FastAPI:
    viewer = RunViewer(entries)
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
    def list_journal_entries(run_id: str) -> list[JournalEntry]:
        journal = viewer.list_journal_entries(run_id)
        if not journal:
            raise HTTPException(status_code=404, detail="Run not found")
        return journal

    return app
