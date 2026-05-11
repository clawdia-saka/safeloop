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

KNOWN_EVIDENCE = (
    ("api_trace", "api-trace.json"),
    ("side_effects", "side-effects-ledger.json"),
    ("pr_lifecycle", "pr-lifecycle.json"),
)


def build_delta_audit_packet(run_dir: str | Path, *, output_dir: str | Path) -> dict[str, Any]:
    """Build a local packet from evidence files already present in ``run_dir``.

    Missing optional evidence files are not gaps. A present evidence file becomes
    action-required only when it cannot be parsed as JSON.
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
            continue
        try:
            raw = evidence_path.read_bytes()
            payload = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            issues.append(f"malformed evidence {filename}")
            continue

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

    _write_json(out_path / "manifest.json", manifest)
    _write_json(out_path / "evidence-bundle.json", bundle)
    (out_path / "brief.md").write_text(brief, encoding="utf-8")

    return {
        "schema_version": SCHEMA_VERSION,
        "output_dir": str(out_path),
        "source_evidence": bound,
        "action_required": action_required,
        "issues": issues,
    }


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
