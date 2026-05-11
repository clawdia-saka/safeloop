"""Local delta-audit packet assembly.

This module intentionally binds evidence that already exists in a run directory.
It does not collect live API/GitHub data or talk to external services.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "delta-audit-packet.v1"
EVIDENCE_BUNDLE_SCHEMA_VERSION = "delta-audit-evidence-bundle.v1"
REQUIRED_API_TRACE_STAGES = ("request", "runtime", "enforcement", "response")

KNOWN_EVIDENCE = (
    ("api_trace", "api-trace.json"),
    ("side_effects", "side-effects-ledger.json"),
    ("pr_lifecycle", "pr-lifecycle.json"),
)


def build_delta_audit_packet(run_dir: str | Path, *, output_dir: str | Path) -> dict[str, Any]:
    """Build a local packet from evidence files already present in ``run_dir``.

    ``api-trace.json`` is required for this product/API trace slice. Other known
    evidence files are bound when present. A present evidence file becomes
    action-required when it cannot be parsed as JSON or lacks required structure.
    """

    run_path = Path(run_dir)
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    issues: list[str] = []
    bound: list[dict[str, Any]] = []
    artifacts: dict[str, Any] = {}

    for kind, filename in KNOWN_EVIDENCE:
        evidence_path = run_path / filename
        if not evidence_path.exists():
            if kind == "api_trace":
                issues.append(f"missing evidence {filename}")
            continue
        try:
            raw = evidence_path.read_bytes()
            payload = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            issues.append(f"malformed evidence {filename}")
            continue

        if kind == "api_trace":
            issues.extend(_validate_api_trace(payload))

        descriptor = {
            "kind": kind,
            "path": filename,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
        }
        bound.append(descriptor)
        artifacts[kind] = {
            "path": filename,
            "sha256": descriptor["sha256"],
            "payload": payload,
        }

    action_required = bool(issues)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "run_dir": str(run_path),
        "source_evidence": bound,
        "action_required": action_required,
        "issues": issues,
        "packet_files": ["manifest.json", "evidence-bundle.json", "brief.md"],
        "packet_file_digests": {},
    }
    bundle = {
        "schema_version": EVIDENCE_BUNDLE_SCHEMA_VERSION,
        "run_dir": str(run_path),
        "bound_evidence": bound,
        "artifacts": artifacts,
        "action_required": action_required,
        "issues": issues,
    }
    brief = _render_brief(bound, issues, action_required)

    _write_json(out_path / "evidence-bundle.json", bundle)
    (out_path / "brief.md").write_text(brief, encoding="utf-8")
    manifest["packet_file_digests"] = {
        "evidence-bundle.json": _sha256_file(out_path / "evidence-bundle.json"),
        "brief.md": _sha256_file(out_path / "brief.md"),
    }
    _write_json(out_path / "manifest.json", manifest)

    return {
        "schema_version": SCHEMA_VERSION,
        "output_dir": str(out_path),
        "source_evidence": bound,
        "action_required": action_required,
        "issues": issues,
    }


def verify_delta_audit_packet(packet_dir: str | Path) -> dict[str, Any]:
    packet_path = Path(packet_dir)
    issues: list[str] = []
    try:
        manifest = json.loads((packet_path / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {"status": "invalid", "issues": ["missing-or-malformed manifest.json"]}

    if manifest.get("schema_version") != SCHEMA_VERSION:
        issues.append("manifest schema mismatch")
    digests = manifest.get("packet_file_digests")
    if not isinstance(digests, dict):
        issues.append("packet-file-digests malformed")
        digests = {}
    for name in ("evidence-bundle.json", "brief.md"):
        expected = digests.get(name)
        if not isinstance(expected, str) or not _is_hex_sha256(expected):
            issues.append(f"packet-file-hash-malformed {name}")
            continue
        path = packet_path / name
        if not path.exists():
            issues.append(f"packet-file-missing {name}")
            continue
        if _sha256_file(path) != expected:
            issues.append(f"packet-file-hash-mismatch {name}")
    return {"status": "invalid" if issues else "valid", "issues": issues}


def _validate_api_trace(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return ["api-trace malformed schema"]
    events = payload.get("events")
    if not isinstance(events, list):
        return ["api-trace malformed schema"]
    stages: dict[str, dict[str, Any]] = {}
    for event in events:
        if not isinstance(event, dict):
            continue
        stage = event.get("stage")
        if isinstance(stage, str):
            stages[stage] = event
    issues: list[str] = []
    for stage in REQUIRED_API_TRACE_STAGES:
        event = stages.get(stage)
        if event is None:
            issues.append(f"api-trace missing digest-bound stage {stage}")
            continue
        if not _has_digest_binding(event):
            issues.append(f"api-trace missing digest-bound stage {stage}")
    return issues


def _has_digest_binding(event: dict[str, Any]) -> bool:
    digest = event.get("artifact_sha256")
    hash_value = event.get("hash") or event.get("digest") or event.get("sha256")
    return isinstance(hash_value, str) and bool(hash_value.strip()) and isinstance(digest, str) and _is_hex_sha256(digest)


def _is_hex_sha256(value: str) -> bool:
    if len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _render_brief(bound: list[dict[str, Any]], issues: list[str], action_required: bool) -> str:
    lines = [
        "## Delta audit packet",
        "",
        f"Action required: {'yes' if action_required else 'no'}",
        "",
        "### Bound source evidence",
    ]
    if bound:
        for item in bound:
            lines.append(f"- {item['kind']}: {item['path']} ({item['sha256']})")
    else:
        lines.append("- none")
    lines.extend(["", "### Issues"])
    if issues:
        lines.extend(f"- {issue}" for issue in issues)
    else:
        lines.append("- none")
    lines.append("")
    return "\n".join(lines)
