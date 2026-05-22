"""Action span ledger API.

This module intentionally writes a child-process-owned ledger separate from the
watchdog timeline so watched agents never append to timeline.jsonl.
"""
from __future__ import annotations

import hashlib
import difflib
import json
import os
import re
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from safeloop.storage import exclusive_lock

ACTION_EVENTS_FILENAME = "action-events.jsonl"
SCHEMA_VERSION = "action-event.v1"
VALID_EVENT_TYPES = {"action_started", "action_finished"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha_event(event_without_hash: dict[str, Any]) -> str:
    data = json.dumps(event_without_hash, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _is_sha(value: Any) -> bool:
    return isinstance(value, str) and value.startswith("sha256:") and len(value) == 71


def _snapshot_text(root: Path) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for path in root.rglob("*"):
        if not path.is_file() or ".safeloop" in path.parts:
            continue
        rel = path.relative_to(root).as_posix()
        try:
            out[rel] = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            out[rel] = None
    return out


def _parse_hunk_header(line: str) -> tuple[int, int, int, int] | None:
    match = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2) or "1"), int(match.group(3)), int(match.group(4) or "1")


def _action_changes(root: Path, before: dict[str, str | None]) -> tuple[list[str], list[dict[str, Any]]]:
    after = _snapshot_text(root)
    files = sorted(f for f in set(before) | set(after) if before.get(f) != after.get(f))
    hunks: list[dict[str, Any]] = []
    for rel in files:
        if before.get(rel) is None or after.get(rel) is None:
            continue
        old_lines = str(before[rel]).splitlines(keepends=True)
        new_lines = str(after[rel]).splitlines(keepends=True)
        for line in difflib.unified_diff(old_lines, new_lines, lineterm=""):
            parsed = _parse_hunk_header(line)
            if parsed:
                os_, ol, ns, nl = parsed
                hunks.append({"path": rel, "old_start": os_, "old_lines": ol, "new_start": ns, "new_lines": nl})
    return files, hunks


def _ledger_path_from_env() -> tuple[Path, str] | None:
    run_dir = os.environ.get("SAFELOOP_RUN_DIR")
    run_id = os.environ.get("SAFELOOP_RUN_ID")
    if not run_dir or not run_id:
        return None
    return Path(run_dir) / ACTION_EVENTS_FILENAME, run_id


def _read_last_hash(path: Path) -> str | None:
    if not path.exists():
        return None
    last: str | None = None
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                last = json.loads(line).get("event_hash")
            except json.JSONDecodeError:
                # Preserve append-only behavior; verifier reports malformed data.
                return last
    return last


def append_action_event(path: Path, event: dict[str, Any]) -> str:
    """Append one hash-chained action event under an inter-process file lock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+", encoding="utf-8") as lock_handle:
        with exclusive_lock(lock_handle):
            event["prev_event_hash"] = _read_last_hash(path)
            event["event_hash"] = _sha_event(event)
            with path.open("a", encoding="utf-8") as ledger:
                ledger.write(json.dumps(event, sort_keys=True) + "\n")
            return event["event_hash"]


@contextmanager
def action_span(name: str, *, intent: str | None = None) -> Iterator[str | None]:
    """Record an action_started/action_finished pair when running under watch_run.

    Without SAFELOOP_RUN_DIR and SAFELOOP_RUN_ID, this is a no-op context manager.
    The yielded value is the action_id when recorded, else None.
    """
    target = _ledger_path_from_env()
    if target is None:
        yield None
        return

    path, run_id = target
    action_id = "act-" + uuid.uuid4().hex
    common = {
        "schema_version": SCHEMA_VERSION,
        "action_id": action_id,
        "name": name,
        "intent": intent,
        "run_id": run_id,
    }
    before = _snapshot_text(Path.cwd())
    append_action_event(path, {**common, "event_type": "action_started", "timestamp": _now()})
    previous_action_id = os.environ.get("SAFELOOP_ACTION_ID")
    os.environ["SAFELOOP_ACTION_ID"] = action_id
    try:
        yield action_id
    finally:
        try:
            files, hunks = _action_changes(Path.cwd(), before)
            append_action_event(
                path,
                {**common, "event_type": "action_finished", "timestamp": _now(), "files": files, "hunks": hunks},
            )
        finally:
            if previous_action_id is None:
                os.environ.pop("SAFELOOP_ACTION_ID", None)
            else:
                os.environ["SAFELOOP_ACTION_ID"] = previous_action_id


def verify_action_events(path: Path) -> dict[str, Any]:
    issues: list[str] = []
    checked = False
    prev: str | None = None
    open_actions: dict[str, int] = {}
    count = 0
    if not path.exists():
        return {"schema_version": "verify-action-events-result.v1", "status": "missing", "issues": [], "checked_artifacts": []}
    try:
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                checked = True
                count += 1
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as exc:
                    issues.append(f"action-events malformed json line {line_number}: {exc}")
                    continue
                event_hash = event.get("event_hash")
                without_hash = dict(event)
                without_hash.pop("event_hash", None)
                expected = _sha_event(without_hash)
                if event.get("schema_version") != SCHEMA_VERSION:
                    issues.append(f"action-events schema mismatch line {line_number}")
                if event.get("event_type") not in VALID_EVENT_TYPES:
                    issues.append(f"action-events invalid type line {line_number}")
                if event.get("prev_event_hash") != prev:
                    issues.append(f"action-events prev hash mismatch line {line_number}")
                if event_hash != expected:
                    issues.append(f"action-events hash mismatch line {line_number}")
                if not _is_sha(event_hash):
                    issues.append(f"action-events malformed event_hash line {line_number}")
                action_id = event.get("action_id")
                if not isinstance(action_id, str) or not action_id:
                    issues.append(f"action-events missing action_id line {line_number}")
                elif event.get("event_type") == "action_started":
                    open_actions[action_id] = line_number
                elif event.get("event_type") == "action_finished":
                    open_actions.pop(action_id, None)
                for key in ["name", "run_id", "timestamp"]:
                    if not isinstance(event.get(key), str) or not event.get(key):
                        issues.append(f"action-events missing {key} line {line_number}")
                if "intent" not in event:
                    issues.append(f"action-events missing intent line {line_number}")
                prev = event_hash
        for action_id, line_number in sorted(open_actions.items()):
            issues.append(f"action-events missing finish for {action_id} started line {line_number}")
    except OSError as exc:
        issues.append(f"action-events read error: {exc}")
    status = "invalid" if issues else "valid"
    return {
        "schema_version": "verify-action-events-result.v1",
        "status": status,
        "issues": issues,
        "checked_artifacts": [str(path)] if checked else [],
        "event_count": count,
        "latest_event_hash": prev,
    }
