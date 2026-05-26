from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from safeloop.agent_watchdog import atomic_json
from safeloop.quarantine import quarantine_manifest_artifacts

SCHEMA_VERSION = "operator-packet-manifest.v1"
DEFAULT_MANIFEST_NAME = "operator-packet-manifest.json"
SOURCE_ARTIFACTS: tuple[tuple[str, bool], ...] = (
    ("run.json", True),
    ("rollback-plan.json", True),
    ("rollback-result.json", False),
    ("runtime-tool-firewall.jsonl", False),
    ("runtime-tool-exec.jsonl", False),
    ("tool-shims/tool-shims.json", False),
    ("external-outbox.json", False),
    ("external-effects.jsonl", False),
    ("compensation-plan.json", False),
    ("compensation-result.json", False),
    ("verification/verify-artifacts-result.json", False),
    ("local-anchor.json", False),
)
BOUNDARY = {
    "exact_local_rollback_only": True,
    "external_exact_rollback": False,
    "external_compensation_manual_review_only": True,
    "runtime_unknown_tool_manual_review": True,
    "tamper_evident_local_only": True,
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_file(path: Path) -> str | None:
    if not path.exists():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


def _relative_to_run(run_path: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(run_path.resolve()).as_posix()
    except ValueError:
        return str(path)


def _safe_run_relative_path(rel_path: str) -> str:
    rel = rel_path.replace("\\", "/")
    parts = Path(rel).parts
    if Path(rel).is_absolute() or rel in {"", "."} or ".." in parts:
        raise ValueError(f"artifact path must stay under run directory: {rel_path}")
    return rel


def _safe_run_file(run_path: Path, rel_path: str) -> Path:
    rel = _safe_run_relative_path(rel_path)
    base = run_path.resolve()
    candidate = run_path / rel
    current = run_path
    for part in Path(rel).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"artifact path must not contain symlinks: {rel_path}")
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(base)
    except ValueError as exc:
        raise ValueError(f"artifact path must stay under run directory: {rel_path}") from exc
    if candidate.exists() and not candidate.is_file():
        raise ValueError(f"artifact path must be a regular file: {rel_path}")
    return candidate


def _safe_sha256_run_file(run_path: Path, rel_path: str) -> str | None:
    return _sha256_file(_safe_run_file(run_path, rel_path))


def validate_operator_packet_manifest_packet_path(run_dir: str | Path, packet_path: str | Path) -> None:
    run_path = Path(run_dir)
    packet = Path(packet_path)
    _safe_run_file(run_path, _relative_to_run(run_path, packet))


def _load_run_id(run_path: Path) -> str | None:
    try:
        data = json.loads((run_path / "run.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    run_id = data.get("run_id")
    return str(run_id) if run_id is not None else None


def _source_artifact_entry(run_path: Path, rel_path: str, required: bool) -> dict[str, Any]:
    digest = _safe_sha256_run_file(run_path, rel_path)
    return {
        "path": rel_path,
        "sha256": digest,
        "required": required,
        "present": digest is not None,
    }


def _expected_source_artifacts(run_path: Path) -> tuple[tuple[str, bool], ...]:
    quarantine_artifacts = tuple((path, True) for path in quarantine_manifest_artifacts(run_path))
    shim_artifacts = tuple(
        (path.relative_to(run_path).as_posix(), True)
        for path in sorted((run_path / "tool-shims" / "bin").glob("*"))
        if path.is_file()
    )
    exec_artifacts = tuple(
        (path.relative_to(run_path).as_posix(), True)
        for path in sorted((run_path / "runtime-tool-exec").glob("*/*.txt"))
        if path.is_file()
    )
    return SOURCE_ARTIFACTS + quarantine_artifacts + shim_artifacts + exec_artifacts


def build_operator_packet_manifest(
    run_dir: str | Path,
    packet_path: str | Path | None = None,
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    run_path = Path(run_dir)
    raw_packet = Path(packet_path) if packet_path is not None else Path("operator-packet-v2.md")
    packet = raw_packet if raw_packet.is_absolute() else run_path / raw_packet
    packet_rel = _relative_to_run(run_path, packet)
    safe_packet = _safe_run_file(run_path, packet_rel)
    packet_digest = _sha256_file(safe_packet)
    return {
        "schema_version": SCHEMA_VERSION,
        "packet_path": packet_rel,
        "packet_sha256": packet_digest,
        "generated_at": generated_at or _utc_now(),
        "run_id": _load_run_id(run_path),
        "source_artifacts": [
            _source_artifact_entry(run_path, rel_path, required)
            for rel_path, required in _expected_source_artifacts(run_path)
        ],
        "boundary": dict(BOUNDARY),
        "verification": {
            "status": "valid" if packet_digest else "invalid",
            "issues": [] if packet_digest else ["packet missing"],
            "verified_at": generated_at or _utc_now(),
        },
    }


def write_operator_packet_manifest(
    run_dir: str | Path,
    packet_path: str | Path | None = None,
    *,
    manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    run_path = Path(run_dir)
    manifest = build_operator_packet_manifest(run_path, packet_path)
    out = Path(manifest_path) if manifest_path is not None else run_path / DEFAULT_MANIFEST_NAME
    atomic_json(out, manifest)
    return manifest


def _load_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def verify_operator_packet_manifest(
    run_dir: str | Path,
    *,
    manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    run_path = Path(run_dir)
    manifest_file = Path(manifest_path) if manifest_path is not None else run_path / DEFAULT_MANIFEST_NAME
    manifest = _load_manifest(manifest_file)
    issues: list[str] = []

    if manifest.get("schema_version") != SCHEMA_VERSION:
        issues.append(f"schema_version mismatch: {manifest.get('schema_version')}")

    packet_rel = str(manifest.get("packet_path") or "operator-packet-v2.md")
    try:
        packet_path = _safe_run_file(run_path, packet_rel)
    except ValueError as exc:
        packet_path = None
        issues.append(str(exc))
    current_packet_sha = _sha256_file(packet_path) if packet_path is not None else None
    if packet_path is not None and current_packet_sha is None:
        issues.append(f"packet missing: {packet_rel}")
    elif packet_path is not None and current_packet_sha != manifest.get("packet_sha256"):
        issues.append("packet_sha256 mismatch")

    if manifest.get("boundary") != BOUNDARY:
        issues.append("boundary mismatch")

    entries = manifest.get("source_artifacts", [])
    if not isinstance(entries, list):
        issues.append("source_artifacts must be a list")
        entries = []
    expected_source_artifacts = _expected_source_artifacts(run_path)
    entries_by_path = {entry.get("path"): entry for entry in entries if isinstance(entry, dict)}
    for rel_path, required in expected_source_artifacts:
        entry = entries_by_path.get(rel_path)
        if entry is None:
            issues.append(f"source artifact entry missing: {rel_path}")
            continue
        if entry.get("required") is not required:
            issues.append(f"source artifact required flag mismatch: {rel_path}")
        try:
            current_sha = _safe_sha256_run_file(run_path, rel_path)
        except ValueError as exc:
            issues.append(str(exc))
            continue
        was_present = entry.get("present") is True
        if current_sha is None:
            if required or was_present:
                issues.append(f"source artifact missing: {rel_path}")
            continue
        if not was_present:
            issues.append(f"source artifact appeared after manifest generation: {rel_path}")
            continue
        if current_sha != entry.get("sha256"):
            issues.append(f"source artifact sha256 mismatch: {rel_path}")

    for entry in entries:
        if not isinstance(entry, dict):
            issues.append("invalid source artifact entry")
            continue
        rel_path = str(entry.get("path") or "")
        if rel_path == DEFAULT_MANIFEST_NAME:
            issues.append("manifest must not be part of source_artifacts")
        elif rel_path and rel_path not in {path for path, _ in expected_source_artifacts}:
            issues.append(f"unexpected source artifact entry: {rel_path}")

    verification = {
        "status": "invalid" if issues else "valid",
        "issues": issues,
        "verified_at": _utc_now(),
    }
    result = dict(manifest)
    result["verification"] = verification
    return result
