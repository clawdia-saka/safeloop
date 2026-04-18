from pathlib import Path

import pytest

from safeloop.journal import JournalEntry, JournalState
from safeloop.storage import JournalStorageError, LocalJournalStorage


def test_append_persists_entries_as_jsonl(tmp_path: Path) -> None:
    storage_path = tmp_path / "journal.jsonl"
    storage = LocalJournalStorage(storage_path)
    entry = JournalEntry(
        run_id="run-1",
        action_id="action-1",
        state=JournalState.PROPOSED,
    )

    storage.append(entry)

    assert storage_path.exists()
    assert storage_path.read_text(encoding="utf-8").strip() == entry.model_dump_json()


def test_read_returns_entries_for_single_run_in_append_order(tmp_path: Path) -> None:
    storage = LocalJournalStorage(tmp_path / "journal.jsonl")
    first_entry = JournalEntry(
        run_id="run-1",
        action_id="action-1",
        state=JournalState.PROPOSED,
    )
    second_entry = JournalEntry(
        run_id="run-1",
        action_id="action-2",
        state=JournalState.APPROVED,
    )
    other_run_entry = JournalEntry(
        run_id="run-2",
        action_id="action-3",
        state=JournalState.FAILED,
    )

    storage.append(first_entry)
    storage.append(other_run_entry)
    storage.append(second_entry)

    assert storage.read("run-1") == [first_entry, second_entry]


def test_list_run_ids_returns_unique_run_ids_in_first_seen_order(tmp_path: Path) -> None:
    storage = LocalJournalStorage(tmp_path / "journal.jsonl")

    storage.append(
        JournalEntry(run_id="run-2", action_id="action-1", state=JournalState.PROPOSED)
    )
    storage.append(
        JournalEntry(run_id="run-1", action_id="action-2", state=JournalState.APPROVED)
    )
    storage.append(
        JournalEntry(run_id="run-2", action_id="action-3", state=JournalState.APPLIED)
    )

    assert storage.list_run_ids() == ["run-2", "run-1"]


def test_storage_reloads_existing_entries_from_disk(tmp_path: Path) -> None:
    storage_path = tmp_path / "journal.jsonl"
    entry = JournalEntry(
        run_id="run-1",
        action_id="action-1",
        state=JournalState.EXECUTING,
    )

    LocalJournalStorage(storage_path).append(entry)

    reloaded_storage = LocalJournalStorage(storage_path)
    assert reloaded_storage.read("run-1") == [entry]


def test_read_returns_empty_list_when_storage_file_does_not_exist(tmp_path: Path) -> None:
    storage = LocalJournalStorage(tmp_path / "missing.jsonl")

    assert storage.read("run-1") == []


def test_list_run_ids_returns_empty_list_when_storage_file_does_not_exist(tmp_path: Path) -> None:
    storage = LocalJournalStorage(tmp_path / "missing.jsonl")

    assert storage.list_run_ids() == []


def test_read_raises_clear_error_for_malformed_jsonl_line(tmp_path: Path) -> None:
    storage_path = tmp_path / "journal.jsonl"
    storage_path.write_text(
        '{"run_id":"run-1","action_id":"action-1","state":"proposed"}\nnot-json\n',
        encoding="utf-8",
    )

    storage = LocalJournalStorage(storage_path)

    with pytest.raises(JournalStorageError, match=r"Malformed journal entry .* line 2"):
        storage.read("run-1")


def test_list_run_ids_raises_clear_error_for_invalid_entry_shape(tmp_path: Path) -> None:
    storage_path = tmp_path / "journal.jsonl"
    storage_path.write_text(
        '{"run_id":"run-1","action_id":"action-1","state":"proposed"}\n'
        '{"run_id":"run-2","action_id":"action-2"}\n',
        encoding="utf-8",
    )

    storage = LocalJournalStorage(storage_path)

    with pytest.raises(JournalStorageError, match=r"Malformed journal entry .* line 2"):
        storage.list_run_ids()
