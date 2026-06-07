"""Canonical local anchor and artifact hash helpers.

These helpers are intentionally local-only: they bind a SafeLoop approval
payload, run metadata, journal, and artifacts to deterministic sha256 digests
without depending on any remote service or control-plane API.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any

_HASH_PREFIX = "sha256:"


def canonical_json_bytes(payload: Any) -> bytes:
    """Return deterministic UTF-8 JSON bytes for hashable payloads."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def canonical_sha256(payload: Any) -> str:
    """Hash a JSON-serializable payload using SafeLoop canonical JSON."""
    return _HASH_PREFIX + hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def approval_payload_hash(payload: dict[str, Any]) -> str:
    """Canonical sha256 binding for a human/agent approval payload."""
    return canonical_sha256(payload)


def artifact_hash(path: Path) -> str:
    """Hash artifact bytes exactly as stored on disk."""
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return _HASH_PREFIX + h.hexdigest()


def journal_hash(path: Path) -> str:
    """Hash a JSONL journal deterministically by canonicalizing each event line.

    Blank lines are ignored. This catches event field tampering while remaining
    stable across JSON object key order in individual journal lines.
    """
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return canonical_sha256(events)


def run_artifact_hashes(run_dir: Path) -> dict[str, str]:
    """Return hashes for core local run artifacts, excluding derived reports."""
    hashes: dict[str, str] = {}
    for path in sorted(p for p in run_dir.rglob("*") if p.is_file() and not p.is_symlink()):
        rel = path.relative_to(run_dir).as_posix()
        if (
            rel == "local-anchor.json"
            or rel == "operator-packet-manifest.json"
            or rel == "operator-packet-v2.md"
            or rel == "operator-packet.md"
            or rel == "rollback-plan.json"
            or rel == "rollback-result.json"
            or rel.startswith("verification/")
            or rel.endswith("/undo-preflight.json")
            or rel.endswith("/undo-result.json")
            or rel.endswith("/rollback-result.json")
        ):
            continue
        hashes[rel] = artifact_hash(path)
    return hashes


def create_local_anchor(run_dir: Path, approval_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Create and persist a local anchor binding a run to its artifacts."""
    anchor = {
        "schema_version": "local-anchor.v1",
        "approval_payload_hash": approval_payload_hash(approval_payload or {}),
        "run_hash": artifact_hash(run_dir / "run.json"),
        "journal_hash": journal_hash(run_dir / "timeline.jsonl"),
        "artifact_hashes": run_artifact_hashes(run_dir),
    }
    anchor["anchor_hash"] = canonical_sha256(anchor)
    anchor_path = run_dir / "local-anchor.json"
    tmp = anchor_path.with_name(f"{anchor_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(anchor, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(anchor_path)
    return anchor


def verify_local_anchor(run_dir: Path) -> dict[str, Any]:
    """Verify local-anchor.json still matches run metadata, journal, and artifacts."""
    issues: list[str] = []
    anchor_path = run_dir / "local-anchor.json"
    if not anchor_path.exists():
        return {"schema_version": "local-anchor-verification.v1", "status": "missing", "issues": ["missing local-anchor.json"]}

    anchor = json.loads(anchor_path.read_text(encoding="utf-8"))
    stored_hash = anchor.get("anchor_hash")
    without_hash = dict(anchor)
    without_hash.pop("anchor_hash", None)
    if stored_hash != canonical_sha256(without_hash):
        issues.append("anchor hash mismatch")
    if not (run_dir / "run.json").exists():
        issues.append("missing run.json")
    elif anchor.get("run_hash") != artifact_hash(run_dir / "run.json"):
        issues.append("run hash mismatch")
    if not (run_dir / "timeline.jsonl").exists():
        issues.append("missing timeline.jsonl")
    elif anchor.get("journal_hash") != journal_hash(run_dir / "timeline.jsonl"):
        issues.append("journal hash mismatch")

    actual_artifacts = run_artifact_hashes(run_dir)
    expected_artifacts = anchor.get("artifact_hashes", {})
    for rel, digest in expected_artifacts.items():
        actual = actual_artifacts.get(rel)
        if actual is None:
            issues.append(f"missing artifact {rel}")
        elif actual != digest:
            issues.append(f"artifact hash mismatch {rel}")
    for rel in sorted(set(actual_artifacts) - set(expected_artifacts)):
        issues.append(f"unbound artifact {rel}")

    return {
        "schema_version": "local-anchor-verification.v1",
        "status": "invalid" if issues else "valid",
        "issues": issues,
        "anchor_hash": stored_hash,
    }
