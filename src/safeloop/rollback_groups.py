from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from safeloop.agent_watchdog import atomic_json, timeline_summary
from safeloop.side_effect_ledger import read_side_effect_events

DOC_EXT = {".md", ".rst", ".txt"}
TEST_PARTS = {"tests", "test"}
DEP_NAMES = {"requirements.txt", "pyproject.toml", "poetry.lock", "package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "Cargo.toml", "Cargo.lock"}
CODE_EXT = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".c", ".cc", ".cpp", ".h", ".hpp"}


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _family(path: str) -> str:
    p = Path(path)
    parts = set(p.parts)
    if p.name in DEP_NAMES:
        return "dependency"
    if parts & TEST_PARTS or p.name.startswith("test_") or p.name.endswith("_test.py"):
        return "tests"
    if p.suffix.lower() in DOC_EXT:
        return "docs"
    if p.suffix.lower() in CODE_EXT:
        return "code"
    return "files"


def _title(family: str) -> str:
    return {"docs": "docs update", "tests": "tests update", "code": "code update", "dependency": "dependency update"}.get(family, "file changes")


def _side_effect_summary(run_dir: Path) -> dict[str, Any]:
    events = read_side_effect_events(run_dir / "side-effects.jsonl")
    return {
        "count": len(events),
        "types": sorted({str(e.get("type") or e.get("kind") or e.get("effect_type") or "unknown") for e in events}),
        "support_status": "manual_review",
        "compensation_required": bool(events),
        "exact_rollback": False,
        "raw_payloads_included": False,
    }


def _checkpoint_data(run_dir: Path) -> dict[str, dict[str, Any]]:
    data: dict[str, dict[str, Any]] = {}
    for cp_dir in sorted((run_dir / "checkpoints").glob("cp-*")) if (run_dir / "checkpoints").exists() else []:
        cid = cp_dir.name
        restore = _load_json(cp_dir / "restore-manifest.json") if (cp_dir / "restore-manifest.json").exists() else {}
        files = sorted(set(restore.get("created_files", []) + restore.get("modified_files", []) + restore.get("deleted_files", [])))
        hmanifest = _load_json(cp_dir / "hunk-manifest.json") if (cp_dir / "hunk-manifest.json").exists() else {"hunks": []}
        hunks = []
        for h in hmanifest.get("hunks", []):
            hunks.append({
                "hunk_id": h.get("hunk_id"),
                "checkpoint_id": h.get("checkpoint_id", cid),
                "path": h.get("path"),
                "operation": h.get("operation"),
                "undo_supported": h.get("undo_supported", True),
                "unsupported_reason": h.get("unsupported_reason"),
                "binary": h.get("binary", False),
            })
            if h.get("path") and h.get("path") not in files:
                files.append(h.get("path"))
        data[cid] = {"files": sorted(files), "hunks": hunks}
    return data


def _action_events(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "action-events.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            item = json.loads(line)
            if item.get("action_id"):
                out.append(item)
    return out


def _make_group(idx: int, *, title: str, source: str, files: list[str], checkpoints: list[str], hunks: list[dict[str, Any]], run_dir: Path, action_id: str | None = None) -> dict[str, Any]:
    side_effects = _side_effect_summary(run_dir)
    unsupported = [h for h in hunks if h.get("undo_supported") is False or h.get("binary") or h.get("unsupported_reason")]
    risk = "high" if unsupported else ("medium" if side_effects["count"] else "low")
    blockers: list[str] = []
    if unsupported:
        blockers.append("unsupported_hunk")
    modes = ["checkpoint", "file"]
    if hunks:
        modes.append("hunk")
    if source == "action":
        modes.append("action")
    g = {
        "group_id": f"grp-{idx:04d}",
        "title": title,
        "reason": source,
        "source": source,
        "files": sorted(set(files)),
        "hunks": hunks,
        "checkpoints": checkpoints,
        "side_effects": {"count": side_effects["count"], "types": side_effects["types"]},
        "external_side_effects": {"classification": "manual_review", "exact_rollback": False},
        "rollback_modes": modes,
        "risk": risk,
        "blockers": blockers,
        "dependency_warnings": [],
        "exact_rollback_available": not bool(unsupported) and side_effects["count"] == 0,
    }
    if action_id:
        g["action_id"] = action_id
    if side_effects["count"]:
        g["dependency_warnings"].append("external_side_effects_require_manual_review")
        g["exact_rollback_available"] = False
    return g


def build_rollback_groups(run_dir: Path) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    run = _load_json(run_dir / "run.json")
    cpdata = _checkpoint_data(run_dir)
    groups: list[dict[str, Any]] = []
    actions = _action_events(run_dir)
    if actions:
        for i, ev in enumerate(actions, 1):
            cps = [ev.get("checkpoint_id")] if ev.get("checkpoint_id") else sorted(cpdata)
            files = list(ev.get("files") or [])
            hunks = [h for cid in cps for h in cpdata.get(cid, {}).get("hunks", []) if not files or h.get("path") in files]
            groups.append(_make_group(i, title=str(ev.get("title") or ev["action_id"]), source="action", files=files or [h.get("path") for h in hunks if h.get("path")], checkpoints=cps, hunks=hunks, run_dir=run_dir, action_id=str(ev["action_id"])))
    else:
        idx = 1
        for cid, data in cpdata.items():
            fams = { _family(f) for f in data["files"] }
            if len(fams) > 1 and len(data["files"]) > 3:
                for fam in ["dependency", "docs", "tests", "code", "files"]:
                    files = [f for f in data["files"] if _family(f) == fam]
                    if files:
                        hunks = [h for h in data["hunks"] if h.get("path") in files]
                        groups.append(_make_group(idx, title=_title(fam), source="file-family", files=files, checkpoints=[cid], hunks=hunks, run_dir=run_dir)); idx += 1
            else:
                fam = next(iter(fams), "files")
                groups.append(_make_group(idx, title=_title(fam), source="checkpoint", files=data["files"], checkpoints=[cid], hunks=data["hunks"], run_dir=run_dir)); idx += 1
    artifact = {
        "schema_version": "rollback-groups.v1",
        "run": {"run_id": run.get("run_id"), "task_id": run.get("task_id"), "status": run.get("status"), "checkpoint_count": run.get("checkpoint_count")},
        "groups": groups,
        "side_effects": _side_effect_summary(run_dir),
    }
    atomic_json(run_dir / "rollback-groups.json", artifact)
    return artifact


def print_rollback_groups(artifact: dict[str, Any]) -> None:
    print("SafeLoop rollback groups")
    run = artifact["run"]
    print(f"run id: {run['run_id']}")
    print(f"task id: {run['task_id']}")
    for g in artifact["groups"]:
        print(f"{g['group_id']} {g['title']}")
        print(f"  files: {', '.join(g['files']) if g['files'] else '-'}")
        print(f"  rollback: {', '.join(g['rollback_modes'])}")
        print(f"  risk: {g['risk']}")
        if g["blockers"]:
            print(f"  blockers: {', '.join(g['blockers'])}")
