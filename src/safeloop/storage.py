"""Local file-backed storage for journal entries."""

from __future__ import annotations

import json
from pathlib import Path

from safeloop.journal import JournalEntry


class LocalJournalStorage:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def append(self, entry: JournalEntry) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(entry.model_dump_json())
            handle.write("\n")

    def read(self, run_id: str) -> list[JournalEntry]:
        return [entry for entry in self._iter_entries() if entry.run_id == run_id]

    def list_run_ids(self) -> list[str]:
        run_ids: list[str] = []
        seen_run_ids: set[str] = set()

        for entry in self._iter_entries():
            if entry.run_id in seen_run_ids:
                continue
            seen_run_ids.add(entry.run_id)
            run_ids.append(entry.run_id)

        return run_ids

    def _iter_entries(self) -> list[JournalEntry]:
        if not self.path.exists():
            return []

        entries: list[JournalEntry] = []
        with self.path.open(encoding="utf-8") as handle:
            for line in handle:
                record = line.strip()
                if not record:
                    continue
                entries.append(JournalEntry.model_validate(json.loads(record)))

        return entries
