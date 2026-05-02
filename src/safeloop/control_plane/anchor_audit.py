"""Tamper-evident local audit for control-plane anchor JSONL exports."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from safeloop.local_anchor import artifact_hash, canonical_sha256

SCHEMA_VERSION = "control-plane-audit.v1"
ANCHOR_SCHEMA_VERSION = "control-plane-anchor.v1"


def _rel(path: Path, base: Path | None = None) -> str:
    if base is not None:
        try:
            return path.relative_to(base).as_posix()
        except ValueError:
            pass
    return path.name


def _row_digest(row: sqlite3.Row) -> str:
    return canonical_sha256(dict(row))


def expected_anchor_records(
    db_path: str | Path,
    *,
    artifacts: Iterable[str | Path] | None = None,
    artifact_base: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Build expected local anchor records from control-plane DB rows and artifacts."""
    db_path = Path(db_path)
    base = Path(artifact_base) if artifact_base is not None else None
    records: list[dict[str, Any]] = []
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        approvals = conn.execute(
            """
            SELECT approval_id, requested_by, action, subject, status,
                   signed_payload, signature, created_at
            FROM approvals ORDER BY created_at ASC, approval_id ASC
            """
        ).fetchall()
        events = conn.execute(
            """
            SELECT event_id, approval_id, event_type, actor, payload, created_at
            FROM approval_events ORDER BY created_at ASC, event_id ASC
            """
        ).fetchall()
    for row in approvals:
        records.append(
            {
                "schema_version": ANCHOR_SCHEMA_VERSION,
                "kind": "approval",
                "id": row["approval_id"],
                "digest": _row_digest(row),
                "created_at": row["created_at"],
            }
        )
    for row in events:
        records.append(
            {
                "schema_version": ANCHOR_SCHEMA_VERSION,
                "kind": "event",
                "id": row["event_id"],
                "digest": _row_digest(row),
                "created_at": row["created_at"],
            }
        )
    for artifact in artifacts or []:
        path = Path(artifact)
        records.append(
            {
                "schema_version": ANCHOR_SCHEMA_VERSION,
                "kind": "artifact",
                "id": _rel(path, base),
                "digest": artifact_hash(path),
                "created_at": None,
            }
        )
    records.sort(key=lambda r: (r.get("created_at") or "9999", r["kind"], r["id"]))
    for seq, record in enumerate(records, start=1):
        record["seq"] = seq
        record["record_hash"] = canonical_sha256({k: v for k, v in record.items() if k != "record_hash"})
    return records


def write_anchor_jsonl(path: str | Path, records: Iterable[dict[str, Any]]) -> None:
    """Write anchor records in deterministic JSONL form."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n" for r in records), encoding="utf-8")


def _read_anchor_jsonl(path: Path, issues: list[str]) -> list[dict[str, Any]]:
    if not path.exists():
        issues.append(f"missing anchor jsonl {path}")
        return []
    records = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            issues.append(f"malformed anchor jsonl line {lineno}")
            continue
        if not isinstance(record, dict):
            issues.append(f"malformed anchor jsonl line {lineno}")
            continue
        records.append(record)
    return records


def audit_control_plane_anchors(
    db_path: str | Path,
    anchor_jsonl: str | Path,
    *,
    output_dir: str | Path,
    artifacts: Iterable[str | Path] | None = None,
    artifact_base: str | Path | None = None,
) -> dict[str, Any]:
    """Compare anchor JSONL with local control-plane DB/event/artifact digests and emit reports."""
    db_path = Path(db_path)
    anchor_jsonl = Path(anchor_jsonl)
    output_dir = Path(output_dir)
    issues: list[str] = []
    expected = expected_anchor_records(db_path, artifacts=artifacts, artifact_base=artifact_base)
    observed = _read_anchor_jsonl(anchor_jsonl, issues)
    expected_by_key = {(r["kind"], r["id"]): r for r in expected}
    observed_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    last_seq = 0
    for record in observed:
        key = (str(record.get("kind")), str(record.get("id")))
        observed_by_key[key] = record
        seq = record.get("seq")
        if not isinstance(seq, int) or seq <= last_seq:
            issues.append(f"reordered anchor {key[0]}:{key[1]}")
        elif key in expected_by_key and seq != expected_by_key[key]["seq"]:
            issues.append(f"reordered anchor {key[0]}:{key[1]}")
        if isinstance(seq, int):
            last_seq = seq
        stored_record_hash = record.get("record_hash")
        without_hash = dict(record)
        without_hash.pop("record_hash", None)
        if stored_record_hash and stored_record_hash != canonical_sha256(without_hash):
            issues.append(f"record hash mismatch {key[0]}:{key[1]}")
    for key, expected_record in expected_by_key.items():
        observed_record = observed_by_key.get(key)
        label = f"{key[0]}:{key[1]}"
        if observed_record is None:
            issues.append(f"missing anchor {label}")
            continue
        if observed_record.get("digest") != expected_record.get("digest"):
            issues.append(f"digest mismatch {label}")
        if expected_record.get("created_at") and observed_record.get("created_at") != expected_record.get("created_at"):
            issues.append(f"stale anchor {label}")
        elif key[0] == "artifact" and observed_record.get("created_at") is not None:
            issues.append(f"stale anchor {label}")
    for key in sorted(set(observed_by_key) - set(expected_by_key)):
        issues.append(f"unexpected anchor {key[0]}:{key[1]}")
    result = {
        "schema_version": SCHEMA_VERSION,
        "description": "tamper-evident local audit",
        "status": "invalid" if issues else "valid",
        "db_path": str(db_path),
        "anchor_jsonl": str(anchor_jsonl),
        "summary": {"expected": len(expected), "observed": len(observed), "issues": len(issues)},
        "issues": issues,
        "expected_anchor_hash": canonical_sha256(expected),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "control-plane-audit.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    issue_lines = "\n".join(f"- {issue}" for issue in issues) if issues else "- none"
    (output_dir / "control-plane-audit.md").write_text(
        "## SafeLoop control-plane tamper-evident local audit\n\n"
        f"status: {result['status']}\n\n"
        f"expected anchors: {len(expected)}\n\n"
        f"observed anchors: {len(observed)}\n\n"
        "issues:\n"
        f"{issue_lines}\n",
        encoding="utf-8",
    )
    return result
