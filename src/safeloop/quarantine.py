"""Reversible local file quarantine for destructive cleanup actions."""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from safeloop.agent_watchdog import atomic_json, now, sha_file
from safeloop.local_anchor import create_local_anchor

ITEM_SCHEMA_VERSION = "quarantine-item.v1"
TOMBSTONE_SCHEMA_VERSION = "quarantine-tombstone.v1"
RESTORE_SCHEMA_VERSION = "restore-manifest.v1"
AUDIT_SCHEMA_VERSION = "quarantine-audit-event.v1"
INDEX_SCHEMA_VERSION = "quarantine-index-entry.v1"


class QuarantineError(ValueError):
    """Raised when a quarantine operation would cross a v0 safety boundary."""


def _quarantine_root(run_dir: Path) -> Path:
    return Path(run_dir) / "quarantine"


def _items_root(run_dir: Path) -> Path:
    return _quarantine_root(run_dir) / "items"


def _item_dir(run_dir: Path, item_id: str) -> Path:
    _safe_item_id(item_id)
    return _items_root(run_dir) / item_id


def _safe_item_id(item_id: str) -> str:
    if not re.fullmatch(r"q-[A-Za-z0-9_.-]{8,80}", item_id):
        raise QuarantineError("invalid quarantine item id")
    return item_id


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise QuarantineError(f"missing quarantine artifact: {path.name}") from exc
    except json.JSONDecodeError as exc:
        raise QuarantineError(f"malformed quarantine artifact: {path.name}") from exc
    if not isinstance(data, dict):
        raise QuarantineError(f"malformed quarantine artifact: {path.name}")
    return data


def _workspace_root(path: str | Path | None) -> Path:
    return Path(path or ".").resolve()


def _has_symlink_ancestor(path: Path, stop_at: Path) -> bool:
    current = path
    ancestors: list[Path] = []
    while current != stop_at and current != current.parent:
        ancestors.append(current)
        current = current.parent
    for candidate in reversed(ancestors):
        if candidate.exists() and candidate.is_symlink():
            return True
    return False


def _relative_target(path: str | Path, workspace_root: Path) -> tuple[Path, str]:
    raw = Path(path)
    if raw.is_absolute():
        candidate = raw
    else:
        if ".." in raw.parts:
            raise QuarantineError("path traversal is not allowed")
        candidate = workspace_root / raw
    resolved_root = workspace_root.resolve()
    resolved_candidate = candidate.resolve(strict=False)
    try:
        rel = resolved_candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise QuarantineError("path must stay within workspace root") from exc
    if rel.parts == ():
        raise QuarantineError("workspace root itself cannot be quarantined")
    if ".." in rel.parts:
        raise QuarantineError("path traversal is not allowed")
    return resolved_candidate, rel.as_posix()


def _resolve_restore_path(original_path: str, workspace_root: Path) -> Path:
    rel = Path(original_path)
    if rel.is_absolute():
        raise QuarantineError("absolute restore paths are not allowed")
    if rel.parts == () or ".." in rel.parts:
        raise QuarantineError("path traversal is not allowed")
    resolved_root = workspace_root.resolve()
    target = (resolved_root / rel).resolve(strict=False)
    try:
        target.relative_to(resolved_root)
    except ValueError as exc:
        raise QuarantineError("path must stay within workspace root") from exc
    if _has_symlink_ancestor(target.parent, resolved_root):
        raise QuarantineError("restore path must not contain symlink ancestors")
    if target.exists() and target.is_symlink():
        raise QuarantineError("restore destination symlinks are not supported")
    return target


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def _audit_path(run_dir: Path, item_id: str) -> Path:
    return _item_dir(run_dir, item_id) / "audit.jsonl"


def _append_audit(run_dir: Path, item_id: str, event_type: str, payload: dict[str, Any] | None = None) -> None:
    path = _audit_path(run_dir, item_id)
    seq = 1
    if path.exists():
        seq = sum(1 for _ in path.open(encoding="utf-8")) + 1
    _append_jsonl(
        path,
        {
            "schema_version": AUDIT_SCHEMA_VERSION,
            "event_id": f"qevt-{seq:04d}",
            "item_id": item_id,
            "event_type": event_type,
            "timestamp": now(),
            "payload": payload or {},
        },
    )


def _append_index(run_dir: Path, item: dict[str, Any]) -> None:
    _append_jsonl(
        _quarantine_root(run_dir) / "index.jsonl",
        {
            "schema_version": INDEX_SCHEMA_VERSION,
            "item_id": item["item_id"],
            "status": item["status"],
            "action": item.get("action", "delete_file"),
            "original_path": item.get("original_path"),
            "timestamp": now(),
            "reason": item.get("reason") or item.get("purge_reason"),
            "item_path": f"quarantine/items/{item['item_id']}/item.json",
        },
    )


def _item_json_path(run_dir: Path, item_id: str) -> Path:
    return _item_dir(run_dir, item_id) / "item.json"


def _restore_manifest_path(run_dir: Path, item_id: str) -> Path:
    return _item_dir(run_dir, item_id) / "restore-manifest.json"


def _payload_path(run_dir: Path, item_id: str) -> Path:
    return _item_dir(run_dir, item_id) / "payload" / "file"


def _read_item(run_dir: Path, item_id: str) -> dict[str, Any]:
    item = _load_json(_item_json_path(run_dir, item_id))
    if item.get("item_id") != item_id:
        raise QuarantineError("quarantine item id mismatch")
    return item


def _write_item(run_dir: Path, item: dict[str, Any]) -> None:
    atomic_json(_item_json_path(run_dir, item["item_id"]), item)


def _refresh_local_anchor_if_possible(run_dir: Path) -> None:
    if (run_dir / "run.json").exists() and (run_dir / "timeline.jsonl").exists():
        create_local_anchor(run_dir)


def _new_item_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"q-{stamp}-{uuid.uuid4().hex[:12]}"


def _permissions(path: Path) -> str:
    return oct(path.stat().st_mode & 0o777)


def put_file_in_quarantine(
    path: str | Path,
    *,
    run_dir: str | Path,
    workspace_root: str | Path = ".",
    reason: str,
    actor: str = "unknown",
) -> dict[str, Any]:
    """Copy a regular file into quarantine metadata, then unlink the original last."""
    root = _workspace_root(workspace_root)
    raw_target = Path(path)
    lexical_target = raw_target if raw_target.is_absolute() else root / raw_target
    if lexical_target.is_symlink():
        raise QuarantineError("symlinks are not supported in quarantine v0")
    target, rel = _relative_target(path, root)
    if target.is_symlink():
        raise QuarantineError("symlinks are not supported in quarantine v0")
    if not target.exists():
        raise QuarantineError(f"missing file: {rel}")
    if target.is_dir():
        raise QuarantineError("directories are not supported in quarantine v0")
    if not target.is_file():
        raise QuarantineError("only regular files are supported in quarantine v0")
    if _has_symlink_ancestor(target.parent, root):
        raise QuarantineError("quarantine path must not contain symlink ancestors")

    run_path = Path(run_dir)
    item_id = _new_item_id()
    items_root = _items_root(run_path)
    items_root.mkdir(parents=True, exist_ok=True)
    temp_dir = items_root / f".tmp-{item_id}-{os.getpid()}"
    final_dir = items_root / item_id
    if final_dir.exists():
        raise QuarantineError("quarantine item already exists")
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    payload = temp_dir / "payload" / "file"
    payload.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(target, payload)
    source_sha = sha_file(target)
    payload_sha = sha_file(payload)
    if payload_sha != source_sha:
        shutil.rmtree(temp_dir)
        raise QuarantineError("payload copy verification failed")

    st = target.stat()
    item = {
        "schema_version": ITEM_SCHEMA_VERSION,
        "item_id": item_id,
        "status": "retained",
        "action": "delete_file",
        "original_path": rel,
        "captured_at": now(),
        "reason": reason,
        "actor": actor,
        "workspace_root": str(root),
        "mode": oct(st.st_mode & 0o777),
        "sha256_before": source_sha,
        "size_bytes": st.st_size,
        "permissions": oct(st.st_mode & 0o777),
        "restore_supported": True,
        "purge_allowed_after": None,
    }
    restore_manifest = {
        "schema_version": RESTORE_SCHEMA_VERSION,
        "restore_type": "file",
        "original_path": rel,
        "payload_path": "payload/file",
        "sha256": source_sha,
        "permissions": oct(st.st_mode & 0o777),
        "overwrite_default": False,
        "path_policy": {
            "allow_absolute_paths": False,
            "allow_path_traversal": False,
            "require_within_workspace": True,
        },
    }
    atomic_json(temp_dir / "item.json", item)
    atomic_json(temp_dir / "restore-manifest.json", restore_manifest)
    _append_jsonl(
        temp_dir / "audit.jsonl",
        {
            "schema_version": AUDIT_SCHEMA_VERSION,
            "event_id": "qevt-0001",
            "item_id": item_id,
            "event_type": "captured",
            "timestamp": now(),
            "payload": {"actor": actor, "reason": reason, "sha256": source_sha},
        },
    )
    temp_dir.replace(final_dir)
    target.unlink()
    _append_index(run_path, item)
    _refresh_local_anchor_if_possible(run_path)
    return item


def verify_quarantine_item(item_id: str, *, run_dir: str | Path) -> dict[str, Any]:
    run_path = Path(run_dir)
    issues: list[str] = []
    try:
        item = _read_item(run_path, item_id)
    except QuarantineError as exc:
        return {
            "schema_version": "quarantine-verification.v1",
            "item_id": item_id,
            "status": "invalid",
            "issues": [str(exc)],
            "verified_at": now(),
        }
    status = str(item.get("status") or "unknown")
    if item.get("schema_version") not in {ITEM_SCHEMA_VERSION, TOMBSTONE_SCHEMA_VERSION}:
        issues.append("item schema_version mismatch")
    if not (_restore_manifest_path(run_path, item_id).exists()):
        issues.append("missing restore-manifest.json")
    if not (_audit_path(run_path, item_id).exists()):
        issues.append("missing audit.jsonl")
    payload = _payload_path(run_path, item_id)
    if status == "purged":
        if payload.exists():
            issues.append("purged item still has payload")
        result_status = "invalid" if issues else "valid"
    else:
        if not payload.exists():
            issues.append("missing payload file")
            result_status = "invalid"
        else:
            expected = item.get("sha256_before")
            actual = sha_file(payload)
            if actual != expected:
                issues.append("payload sha256 mismatch")
                _mark_tampered(run_path, item, actual)
                result_status = "tampered"
            else:
                result_status = "invalid" if issues else "valid"
    return {
        "schema_version": "quarantine-verification.v1",
        "item_id": item_id,
        "item_status": item.get("status"),
        "status": result_status,
        "issues": issues,
        "verified_at": now(),
    }


def _mark_tampered(run_dir: Path, item: dict[str, Any], actual_sha: str | None) -> None:
    if item.get("status") == "tampered":
        return
    updated = dict(item)
    updated["status"] = "tampered"
    updated["tampered_at"] = now()
    _write_item(run_dir, updated)
    _append_audit(run_dir, updated["item_id"], "tampered", {"actual_sha256": actual_sha, "expected_sha256": item.get("sha256_before")})
    _append_index(run_dir, updated)
    _refresh_local_anchor_if_possible(run_dir)


def restore_quarantine_item(
    item_id: str,
    *,
    run_dir: str | Path,
    workspace_root: str | Path = ".",
    overwrite: bool = False,
    actor: str = "unknown",
) -> dict[str, Any]:
    run_path = Path(run_dir)
    root = _workspace_root(workspace_root)
    item = _read_item(run_path, item_id)
    if item.get("status") == "purged":
        raise QuarantineError("purged quarantine item cannot be restored")
    if item.get("status") == "tampered":
        raise QuarantineError("tampered quarantine item cannot be restored")
    verification = verify_quarantine_item(item_id, run_dir=run_path)
    if verification["status"] != "valid":
        raise QuarantineError("quarantine item verification failed")
    manifest = _load_json(_restore_manifest_path(run_path, item_id))
    if manifest.get("schema_version") != RESTORE_SCHEMA_VERSION:
        raise QuarantineError("restore-manifest schema_version mismatch")
    destination = _resolve_restore_path(str(manifest.get("original_path") or ""), root)
    if destination.exists() and not overwrite:
        raise QuarantineError("destination exists; pass --overwrite to restore over it")
    payload = _payload_path(run_path, item_id)
    expected = manifest.get("sha256")
    if sha_file(payload) != expected:
        _mark_tampered(run_path, item, sha_file(payload))
        raise QuarantineError("payload sha256 mismatch")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(payload, destination)
    permissions = int(str(manifest.get("permissions") or item.get("permissions") or "0o600"), 8)
    destination.chmod(permissions)
    updated = dict(item)
    updated["status"] = "restored"
    updated["restored_at"] = now()
    updated["restore_supported"] = True
    _write_item(run_path, updated)
    _append_audit(run_path, item_id, "restored", {"actor": actor, "overwrite": overwrite, "destination": manifest.get("original_path")})
    _append_index(run_path, updated)
    _refresh_local_anchor_if_possible(run_path)
    return {
        "schema_version": "quarantine-restore-result.v1",
        "item_id": item_id,
        "status": "restored",
        "restored_path": str(destination),
        "overwrite": overwrite,
    }


def purge_quarantine_item(
    item_id: str,
    *,
    run_dir: str | Path,
    reason: str = "manual purge",
    actor: str = "unknown",
) -> dict[str, Any]:
    run_path = Path(run_dir)
    item = _read_item(run_path, item_id)
    if item.get("status") == "purged":
        return {
            "schema_version": "quarantine-purge-result.v1",
            "item_id": item_id,
            "status": "purged",
            "already_purged": True,
        }
    payload = _payload_path(run_path, item_id)
    if payload.exists():
        payload.unlink()
    tombstone = {
        "schema_version": TOMBSTONE_SCHEMA_VERSION,
        "item_id": item_id,
        "status": "purged",
        "action": item.get("action", "delete_file"),
        "original_path": item.get("original_path"),
        "captured_at": item.get("captured_at"),
        "reason": item.get("reason"),
        "actor": item.get("actor"),
        "workspace_root": item.get("workspace_root"),
        "mode": item.get("mode"),
        "sha256_before": item.get("sha256_before"),
        "size_bytes": item.get("size_bytes"),
        "permissions": item.get("permissions"),
        "restore_supported": False,
        "purged_at": now(),
        "purge_reason": reason,
        "purged_by": actor,
    }
    _write_item(run_path, tombstone)
    _append_audit(run_path, item_id, "purged", {"actor": actor, "reason": reason})
    _append_index(run_path, tombstone)
    _refresh_local_anchor_if_possible(run_path)
    return {
        "schema_version": "quarantine-purge-result.v1",
        "item_id": item_id,
        "status": "purged",
        "already_purged": False,
    }


def list_quarantine(run_dir: str | Path) -> dict[str, Any]:
    run_path = Path(run_dir)
    items: list[dict[str, Any]] = []
    root = _items_root(run_path)
    if root.exists():
        for item_json in sorted(root.glob("*/item.json")):
            try:
                item = json.loads(item_json.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            items.append(
                {
                    "item_id": item.get("item_id"),
                    "status": item.get("status"),
                    "action": item.get("action", "delete_file"),
                    "original_path": item.get("original_path"),
                    "captured_at": item.get("captured_at"),
                    "restore_supported": item.get("restore_supported", False),
                    "sha256_before": item.get("sha256_before"),
                }
            )
    items.sort(key=lambda item: (str(item.get("captured_at") or ""), str(item.get("item_id") or "")))
    return {
        "schema_version": "quarantine-list.v1",
        "run_dir": str(run_path),
        "items": items,
    }


def empty_quarantine(
    *,
    run_dir: str | Path,
    older_than: str,
    actor: str = "unknown",
    reason: str = "retention expired",
) -> dict[str, Any]:
    cutoff = datetime.now(timezone.utc) - _parse_age(older_than)
    purged: list[str] = []
    skipped: list[dict[str, str]] = []
    for item in list_quarantine(run_dir)["items"]:
        item_id = str(item["item_id"])
        if item.get("status") != "retained":
            skipped.append({"item_id": item_id, "reason": f"status {item.get('status')} is not retained"})
            continue
        captured_at = _parse_datetime(str(item.get("captured_at") or ""))
        if captured_at is None or captured_at > cutoff:
            skipped.append({"item_id": item_id, "reason": "not older than threshold"})
            continue
        purge_quarantine_item(item_id, run_dir=run_dir, actor=actor, reason=reason)
        purged.append(item_id)
    return {
        "schema_version": "quarantine-empty-result.v1",
        "status": "ok",
        "older_than": older_than,
        "purged": purged,
        "skipped": skipped,
    }


def _parse_age(value: str) -> timedelta:
    match = re.fullmatch(r"(\d+)([dhms])", value.strip())
    if not match:
        raise QuarantineError("older-than must look like 30d, 12h, 10m, or 30s")
    amount = int(match.group(1))
    unit = match.group(2)
    if unit == "d":
        return timedelta(days=amount)
    if unit == "h":
        return timedelta(hours=amount)
    if unit == "m":
        return timedelta(minutes=amount)
    return timedelta(seconds=amount)


def _parse_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def quarantine_manifest_artifacts(run_dir: str | Path) -> list[str]:
    """Return manifest-safe quarantine evidence paths, excluding payload bytes."""
    run_path = Path(run_dir)
    qroot = _quarantine_root(run_path)
    if not qroot.exists():
        return []
    paths: list[str] = ["quarantine/index.jsonl"]
    for item_dir in sorted((qroot / "items").glob("q-*")):
        if not item_dir.is_dir():
            continue
        for name in ("item.json", "restore-manifest.json", "audit.jsonl"):
            paths.append(f"quarantine/items/{item_dir.name}/{name}")
    return paths


def build_quarantine_rollback_summary(run_dir: str | Path) -> dict[str, Any] | None:
    """Summarize quarantine lifecycle state for rollback/operator planning."""
    run_path = Path(run_dir)
    listed = list_quarantine(run_path)
    if not listed["items"]:
        return None

    items: list[dict[str, Any]] = []
    counts = {
        "total": 0,
        "restore_available": 0,
        "already_restored": 0,
        "irreversible": 0,
        "manual_review": 0,
    }
    for listed_item in listed["items"]:
        item_id = str(listed_item.get("item_id") or "")
        try:
            item = _read_item(run_path, item_id)
        except QuarantineError as exc:
            counts["total"] += 1
            counts["manual_review"] += 1
            items.append(
                {
                    "item_id": item_id,
                    "original_path": listed_item.get("original_path"),
                    "status": "invalid",
                    "rollback_mode": "manual_review",
                    "rollback_status": "manual_review_required",
                    "exact_rollback": False,
                    "restore_supported": False,
                    "blockers": [str(exc)],
                    "evidence": [f"quarantine/items/{item_id}/item.json"],
                }
            )
            continue

        status = str(item.get("status") or "unknown")
        original_path = str(item.get("original_path") or "")
        verification = verify_quarantine_item(item_id, run_dir=run_path)
        if verification.get("status") == "tampered":
            status = "tampered"
        blockers = list(verification.get("issues") or [])
        exact_rollback = False
        rollback_mode = "none"
        rollback_status = "manual_review_required"
        restore_command = None
        restore_supported = bool(item.get("restore_supported"))

        if status == "retained" and verification.get("status") == "valid" and restore_supported:
            exact_rollback = True
            rollback_mode = "quarantine_restore"
            rollback_status = "available"
            workspace_root = item.get("workspace_root") or "<workspace-root>"
            restore_command = " ".join(
                [
                    "safeloop",
                    "quarantine",
                    "restore",
                    shlex.quote(item_id),
                    "--run-dir",
                    shlex.quote(str(run_path)),
                    "--workspace-root",
                    shlex.quote(str(workspace_root)),
                ]
            )
            counts["restore_available"] += 1
        elif status == "restored":
            rollback_status = "already_restored"
            rollback_mode = "already_restored"
            counts["already_restored"] += 1
        elif status == "purged":
            rollback_status = "irreversible_payload_purged"
            rollback_mode = "irreversible"
            blockers.append("payload purged")
            counts["irreversible"] += 1
        elif status == "tampered" or verification.get("status") == "tampered":
            rollback_status = "manual_review_required"
            rollback_mode = "manual_review"
            blockers.append("payload tampered")
            counts["manual_review"] += 1
        else:
            counts["manual_review"] += 1

        counts["total"] += 1
        record = {
            "item_id": item_id,
            "original_path": original_path,
            "status": status,
            "rollback_mode": rollback_mode,
            "rollback_status": rollback_status,
            "exact_rollback": exact_rollback,
            "restore_supported": restore_supported,
            "verification_status": verification.get("status"),
            "blockers": sorted(set(str(blocker) for blocker in blockers)),
            "evidence": [
                f"quarantine/items/{item_id}/item.json",
                f"quarantine/items/{item_id}/restore-manifest.json",
                f"quarantine/items/{item_id}/audit.jsonl",
            ],
        }
        if restore_command:
            record["restore_command"] = restore_command
        items.append(record)

    return {
        "schema_version": "quarantine-rollback.v1",
        "status": "review_required" if counts["manual_review"] or counts["irreversible"] else "ok",
        "counts": counts,
        "items": items,
        "note": "Quarantine restore is an explicit local-file rollback path. Purged or tampered payloads are not exact rollback.",
    }
