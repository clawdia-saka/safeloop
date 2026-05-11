from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from safeloop.agent_watchdog import (
    _reject_symlink,
    _reject_symlink_ancestor,
    _safe_repo_path,
    _safe_unlink_for_undo,
    _safe_write_for_undo,
    atomic_json,
    append_event,
    rollback_to_start,
    safe_child_file,
    sha_file,
    snapshot,
    timeline_events,
    timeline_summary,
    undo,
    verify_run,
    watch_run,
)
from safeloop.control_plane.anchor_audit import audit_control_plane_anchors
from safeloop.local_anchor import create_local_anchor, verify_local_anchor
from safeloop.side_effect_ledger import read_side_effect_events


def _rollback_artifact(run_dir: Path, checkpoint_id: str, res: dict, *, apply: bool) -> dict:
    status = res.get("status", "unknown")
    file_counts = res.get("file_counts", {})
    restore_path = run_dir / "checkpoints" / checkpoint_id / "restore-manifest.json"
    restore = json.loads(restore_path.read_text(encoding="utf-8"))
    artifact = {
        "schema_version": "rollback-result.v1" if apply else "rollback-plan.v1",
        "operation": "rollback_to_checkpoint",
        "run_id": res["run_id"],
        "checkpoint_id": res["checkpoint_id"],
        "mode": "apply" if apply else "dry-run",
        "status": status,
        "review_required": True,
        "covered_local_file_changes": {
            "created": sorted(restore.get("created_files", [])),
            "modified": sorted(restore.get("modified_files", [])),
            "deleted": sorted(restore.get("deleted_files", [])),
        },
        "file_counts": file_counts,
        "blockers": res.get("blockers", []),
        "dependency_warning": None if status in {"ok", "applied"} else "rollback blocked until blockers are resolved",
        "external_side_effects": {
            "classification": "manual_review",
            "exact_rollback": False,
            "note": "External side effects are not exact rollback; review side-effects.jsonl for compensation or handoff.",
        },
        "preflight": {
            "status": "pass" if status in {"ok", "applied"} else "blocked",
            "artifact": str(run_dir / "checkpoints" / checkpoint_id / "undo-preflight.json"),
        },
        "apply_command": f"safeloop rollback apply {run_dir} {res['run_id']} {checkpoint_id}",
    }
    if apply:
        artifact["verification"] = verify_run(run_dir)
        artifact["undo_result_path"] = res.get("undo_result_path")
    return artifact


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _append_rollback_event(run_dir: Path, event_type: str, payload: dict) -> None:
    run = _load_json(run_dir / "run.json")
    events = timeline_events(run_dir)
    prev = events[-1]["event_hash"] if events else None
    new_hash = append_event(run_dir / "timeline.jsonl", len(events) + 1, event_type, payload, prev)
    run["latest_event_hash"] = new_hash
    run["final_event_hash"] = new_hash
    atomic_json(run_dir / "run.json", run)
    create_local_anchor(run_dir)


def _plan_target(plan: dict) -> tuple[str, str | None]:
    return ("start", None) if plan.get("operation") == "rollback_to_start" else ("checkpoint", plan.get("checkpoint_id"))


def _target_selection(artifact: dict) -> dict:
    if artifact.get("operation") == "rollback_to_start":
        return {"type": "start"}
    if artifact.get("operation") == "rollback_selected_files":
        return artifact.get("target", {"type": "start"})
    return {"type": "checkpoint", "checkpoint_id": artifact.get("checkpoint_id")}


def _normalize_selected_files(files: list[str]) -> list[str]:
    selected: list[str] = []
    for raw in files:
        rel = raw.replace("\\", "/")
        if rel.startswith("/") or ".." in Path(rel).parts or rel in {"", "."}:
            raise ValueError(f"selected file must be repo-local: {raw}")
        if rel not in selected:
            selected.append(rel)
    return selected


def _normalize_selected_hunks(hunks: list[str]) -> list[str]:
    selected: list[str] = []
    for raw in hunks:
        if not raw.startswith("hunk-") or "/" in raw or ".." in raw or raw in {"", "."}:
            raise ValueError(f"selected hunk must be a hunk id: {raw}")
        if raw not in selected:
            selected.append(raw)
    return selected


def _sha_bytes_local(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _text_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines(keepends=True)


def _hunk_context(lines: list[str], start: int, count: int) -> list[str]:
    if start == 0:
        return []
    return lines[start - 1 : start - 1 + count]


def _load_hunk_sources(run_dir: Path) -> dict[str, tuple[Path, dict]]:
    found: dict[str, tuple[Path, dict]] = {}
    for cp in sorted((run_dir / "checkpoints").glob("cp-*")) if (run_dir / "checkpoints").exists() else []:
        hm_path = cp / "hunk-manifest.json"
        if not hm_path.exists():
            continue
        for hunk in _load_json(hm_path).get("hunks", []):
            hid = str(hunk.get("hunk_id"))
            if hid in found:
                raise ValueError(f"ambiguous hunk id across checkpoints: {hid}")
            found[hid] = (cp, hunk)
    return found


def _selected_hunk_rollback(run_dir: Path, run_id: str, selected_hunks: list[str], *, apply: bool) -> dict:
    run = _load_json(run_dir / "run.json")
    if run.get("run_id") != run_id:
        raise ValueError(f"run_id mismatch: expected {run.get('run_id')}, got {run_id}")
    repo = Path(run["repo"])
    selected = _normalize_selected_hunks(selected_hunks)
    sources = _load_hunk_sources(run_dir)
    blockers: list[dict[str, str]] = []
    chosen: list[tuple[Path, dict]] = []
    for hid in selected:
        if hid not in sources:
            blockers.append({"hunk_id": hid, "code": "missing_hunk", "expected": "hunk-manifest entry", "actual": "missing"})
        else:
            chosen.append(sources[hid])
    affected_files = sorted({h["path"] for _, h in chosen})
    for cp, h in chosen:
        rel = h["path"]
        p = _safe_repo_path(repo, rel)
        blocker = _reject_symlink_ancestor(repo, p, rel) or _reject_symlink(p, rel)
        if blocker:
            blockers.append(blocker); continue
        if h.get("binary"):
            blockers.append({"hunk_id": h["hunk_id"], "file": rel, "code": "binary_hunk_unsupported", "expected": "text hunk", "actual": "binary"}); continue
        if not h.get("undo_supported"):
            blockers.append({"hunk_id": h["hunk_id"], "file": rel, "code": str(h.get("unsupported_reason") or "hunk_unsupported"), "expected": "undo_supported=true", "actual": "false"}); continue
        if h.get("operation") == "deleted":
            blockers.append({"hunk_id": h["hunk_id"], "file": rel, "code": "deleted_file_hunk_unsupported", "expected": "existing file", "actual": "deleted"}); continue
        if not p.exists():
            blockers.append({"hunk_id": h["hunk_id"], "file": rel, "code": "missing_file", "expected": "current file", "actual": "missing"}); continue
        try:
            current = _text_lines(p)
        except UnicodeDecodeError:
            blockers.append({"hunk_id": h["hunk_id"], "file": rel, "code": "binary_or_non_utf8_current", "expected": "utf-8 text", "actual": "non-text"}); continue
        blob = _load_json(cp / "restore-manifest.json").get("before_blobs", {}).get(rel)
        if not blob:
            blockers.append({"hunk_id": h["hunk_id"], "file": rel, "code": "missing_restore_blob", "expected": "before blob", "actual": "missing"}); continue
        try:
            before_lines = _text_lines(safe_child_file(cp, blob))
        except UnicodeDecodeError:
            blockers.append({"hunk_id": h["hunk_id"], "file": rel, "code": "binary_or_non_utf8_before", "expected": "utf-8 text", "actual": "non-text"}); continue
        ns, nl, os, ol = int(h["new_start"]), int(h["new_lines"]), int(h["old_start"]), int(h["old_lines"])
        if ns == 0 or ns - 1 + nl > len(current) or (os != 0 and os - 1 + ol > len(before_lines)):
            blockers.append({"hunk_id": h["hunk_id"], "file": rel, "code": "hunk_context_mismatch", "expected": "hunk range present", "actual": "out_of_bounds"}); continue
        expected_after = h.get("after_text_hash")
        if expected_after and _sha_bytes_local(p.read_bytes()) != expected_after:
            blockers.append({"hunk_id": h["hunk_id"], "file": rel, "code": "hunk_context_mismatch", "expected": str(expected_after), "actual": _sha_bytes_local(p.read_bytes())}); continue
    artifact = {"schema_version": "rollback-result.v1" if apply else "rollback-plan.v1", "operation": "rollback_selected_hunks", "run_id": run_id, "mode": "apply" if apply else "dry-run", "selected_hunks": selected, "affected_files": affected_files, "source": {"checkpoints": sorted({cp.name for cp, _ in chosen})}, "target": {"type": "current_worktree"}, "rollback_source_hashes": {h["path"]: h.get("after_text_hash") for _, h in chosen}, "status": "blocked" if blockers else ("applied" if apply else "ok"), "review_required": True, "blockers": blockers, "dependency_warnings": [], "external_side_effects": {"classification": "manual_review", "exact_rollback": False, "note": "External side effects are not exact rollback; review side-effects.jsonl for compensation or handoff."}, "preflight": {"status": "blocked" if blockers else "pass"}, "file_counts": {"created": 0, "modified": len(affected_files), "deleted": 0}, "apply_command": f"safeloop rollback apply {run_dir} {run_id} --hunks {' '.join(selected)}"}
    if not apply:
        atomic_json(run_dir / "rollback-plan.json", artifact); return artifact
    if blockers:
        artifact["blocked_reason"] = blockers[0]["code"]; atomic_json(run_dir / "rollback-result.json", artifact); return artifact
    by_file: dict[str, list[tuple[Path, dict]]] = {}
    for cp, h in chosen:
        by_file.setdefault(h["path"], []).append((cp, h))
    for rel, items in by_file.items():
        p = _safe_repo_path(repo, rel); lines = _text_lines(p)
        for cp, h in sorted(items, key=lambda item: int(item[1]["new_start"]), reverse=True):
            before_blob = _load_json(cp / "restore-manifest.json")["before_blobs"][rel]
            before_lines = _text_lines(safe_child_file(cp, before_blob))
            ns, nl, os, ol = int(h["new_start"]), int(h["new_lines"]), int(h["old_start"]), int(h["old_lines"])
            lines[ns - 1 : ns - 1 + nl] = _hunk_context(before_lines, os, ol)
        p.write_text("".join(lines), encoding="utf-8")
    artifact["restored_hunk_count"] = len(selected)
    atomic_json(run_dir / "rollback-result.json", artifact)
    artifact["verification"] = verify_run(run_dir, check_source_evidence=False)
    atomic_json(run_dir / "rollback-result.json", artifact)
    return artifact


def _target_from_args(checkpoint_id: str | None) -> dict:
    return {"type": "checkpoint", "checkpoint_id": checkpoint_id} if checkpoint_id else {"type": "start"}


def _selected_rollback(run_dir: Path, run_id: str, selected_files: list[str], *, checkpoint_id: str | None, apply: bool) -> dict:
    run = _load_json(run_dir / "run.json")
    if run.get("run_id") != run_id:
        raise ValueError(f"run_id mismatch: expected {run.get('run_id')}, got {run_id}")
    verification = verify_run(run_dir, check_source_evidence=False)
    if apply and verification["status"] != "valid":
        issues = verification.get("issues", [])
        restore_blob_only = issues and all(str(issue).startswith("restore blob missing ") for issue in issues)
        if not restore_blob_only:
            raise ValueError("cannot run selected-file rollback: artifact verification is not valid")
    repo = Path(run["repo"])
    selected = _normalize_selected_files(selected_files)
    base_dir = run_dir / "baseline"
    manifest = _load_json(base_dir / "manifest.json")
    before = manifest.get("files", {})
    target = _target_from_args(checkpoint_id)
    current = snapshot(repo)
    changed = {f for f in current if f not in before} | {f for f in current if f in before and current[f] != before[f]} | {f for f in before if f not in current}
    source_hashes = {f: current.get(f) for f in selected}
    restore_blobs = {f: b for f, b in manifest.get("before_blobs", {}).items() if f in selected}
    restore_root = base_dir
    if checkpoint_id:
        cp_dir = run_dir / "checkpoints" / checkpoint_id
        restore = _load_json(cp_dir / "restore-manifest.json")
        changed = set(restore.get("created_files", []) + restore.get("modified_files", []) + restore.get("deleted_files", []))
        source_hashes = {f: restore.get("after_hashes", {}).get(f) for f in selected}
        restore_blobs = {f: b for f, b in restore.get("before_blobs", {}).items() if f in selected}
        restore_root = cp_dir
    created = sorted(f for f in selected if f in changed and f not in before)
    deleted = sorted(f for f in selected if f in changed and f in before and source_hashes.get(f) is None)
    modified = sorted(f for f in selected if f in changed and f in before and f not in deleted)
    blockers: list[dict[str, str]] = []
    for f in selected:
        p = _safe_repo_path(repo, f)
        blocker = _reject_symlink_ancestor(repo, p, f) or _reject_symlink(p, f)
        if blocker:
            blockers.append(blocker)
    for f in created + modified:
        if any(b["file"] == f for b in blockers):
            continue
        p = _safe_repo_path(repo, f)
        actual = sha_file(p) if p.exists() else None
        if actual != source_hashes.get(f):
            blockers.append({"file": f, "code": "current_hash_mismatch", "expected": str(source_hashes.get(f)), "actual": str(actual)})
    for f in deleted:
        if any(b["file"] == f for b in blockers):
            continue
        p = _safe_repo_path(repo, f)
        if p.exists():
            blockers.append({"file": f, "code": "deleted_path_recreated", "expected": "absent", "actual": sha_file(p) if p.is_file() else "non_file_exists"})
    for f in modified + deleted:
        blob = restore_blobs.get(f)
        if not blob or not safe_child_file(restore_root, blob).exists():
            blockers.append({"file": f, "code": "missing_restore_blob", "expected": "restore blob", "actual": "missing"})
    touched_counts = {f: 0 for f in selected}
    for cp in sorted((run_dir / "checkpoints").glob("cp-*")) if (run_dir / "checkpoints").exists() else []:
        rm = _load_json(cp / "restore-manifest.json")
        cp_changed = set(rm.get("created_files", []) + rm.get("modified_files", []) + rm.get("deleted_files", []))
        for f in selected:
            if f in cp_changed:
                touched_counts[f] += 1
    dependency_warnings = [{"file": f, "code": "touched_across_multiple_checkpoints"} for f, n in touched_counts.items() if n > 1]
    for f in modified:
        if not any(w["file"] == f for w in dependency_warnings):
            dependency_warnings.append({"file": f, "code": "later_changes_after_target"})
    artifact = {"schema_version": "rollback-result.v1" if apply else "rollback-plan.v1", "operation": "rollback_selected_files", "run_id": run_id, "mode": "apply" if apply else "dry-run", "selected_files": selected, "target": target, "rollback_source_hashes": {f: source_hashes.get(f) for f in selected}, "status": "blocked" if blockers else ("applied" if apply else "ok"), "review_required": True, "covered_local_file_changes": {"created": created, "modified": modified, "deleted": deleted}, "file_counts": {"created": len(created), "modified": len(modified), "deleted": len(deleted)}, "blockers": blockers, "dependency_warnings": dependency_warnings, "external_side_effects": {"classification": "manual_review", "exact_rollback": False, "note": "External side effects are not exact rollback; review side-effects.jsonl for compensation or handoff."}, "preflight": {"status": "blocked" if blockers else "pass"}, "apply_command": f"safeloop rollback apply {run_dir} {run_id} {'--from-checkpoint ' + checkpoint_id + ' ' if checkpoint_id else ''}--files {' '.join(selected)}"}
    if not apply:
        atomic_json(run_dir / "rollback-plan.json", artifact)
        return artifact
    if blockers:
        artifact["blocked_reason"] = blockers[0]["code"]
        atomic_json(run_dir / "rollback-result.json", artifact)
        return artifact
    apply_blockers: list[dict[str, str]] = []
    for f in created:
        blocker = _safe_unlink_for_undo(repo, _safe_repo_path(repo, f), f)
        if blocker:
            apply_blockers.append(blocker)
    for f in modified + deleted:
        blob = restore_blobs.get(f)
        blocker = _safe_write_for_undo(repo, _safe_repo_path(repo, f), f, safe_child_file(restore_root, blob).read_bytes()) if blob else {"file": f, "code": "missing_restore_blob", "expected": "restore blob", "actual": "missing"}
        if blocker:
            apply_blockers.append(blocker)
    if apply_blockers:
        artifact.update({"status": "blocked", "blockers": apply_blockers, "blocked_reason": apply_blockers[0]["code"], "preflight": {"status": "blocked"}})
    else:
        artifact["restored_file_count"] = len(created) + len(modified) + len(deleted)
    atomic_json(run_dir / "rollback-result.json", artifact)
    if artifact["status"] == "applied":
        artifact["verification"] = verify_run(run_dir, check_source_evidence=False)
        atomic_json(run_dir / "rollback-result.json", artifact)
    return artifact


def _require_matching_rollback_plan(run_dir: Path, run_id: str, *, checkpoint_id: str | None, to_start: bool) -> tuple[bool, str | None, dict | None]:
    plan_path = run_dir / "rollback-plan.json"
    if not plan_path.exists():
        return False, "missing rollback-plan.json", None
    try:
        plan = _load_json(plan_path)
    except (OSError, json.JSONDecodeError):
        return False, "malformed rollback-plan.json", None
    checks = [
        (plan.get("schema_version") == "rollback-plan.v1", "schema_version mismatch"),
        (plan.get("run_id") == run_id, "run_id mismatch"),
        (plan.get("mode") == "dry-run", "mode mismatch"),
        (plan.get("preflight", {}).get("status") == "pass", "preflight not pass"),
    ]
    target_type, target_checkpoint = _plan_target(plan)
    if to_start:
        checks.append((target_type == "start", "target mismatch"))
    else:
        checks.append((target_type == "checkpoint" and target_checkpoint == checkpoint_id, "target mismatch"))
    for ok, reason in checks:
        if not ok:
            return False, reason, plan
    return True, None, plan


def _block_rollback_apply(run_dir: Path, run_id: str, *, checkpoint_id: str | None, to_start: bool, reason: str, plan: dict | None) -> dict:
    target = {"type": "start"} if to_start else {"type": "checkpoint", "checkpoint_id": checkpoint_id}
    payload = {"operation": "rollback_to_start" if to_start else "rollback_to_checkpoint", "run_id": run_id, "target": target, "reason": reason, "artifact_digests": {}}
    if plan is not None and (run_dir / "rollback-plan.json").exists():
        payload["artifact_digests"]["rollback-plan.json"] = sha_file(run_dir / "rollback-plan.json")
    _append_rollback_event(run_dir, "rollback_blocked", payload)
    return {"schema_version": "rollback-result.v1", "status": "blocked", "run_id": run_id, "target": target, "preflight": {"status": "blocked"}, "blocked_reason": reason}


def _checkpoint_changes(cp: Path) -> dict[str, list[str]]:
    restore = _load_json(cp / "restore-manifest.json") if (cp / "restore-manifest.json").exists() else {}
    return {
        "created": sorted(restore.get("created_files", [])),
        "modified": sorted(restore.get("modified_files", [])),
        "deleted": sorted(restore.get("deleted_files", [])),
    }


def _side_effect_review(run_dir: Path) -> dict:
    events = read_side_effect_events(run_dir / "side-effects.jsonl")
    count = len(events)
    kinds = sorted({str(e.get("type") or e.get("kind") or e.get("effect_type") or "unknown") for e in events})
    return {
        "count": count,
        "types": kinds,
        "support_status": "manual_review",
        "compensation_required": count > 0,
        "exact_rollback": False,
        "raw_payloads_included": False,
        "note": "External side effects are not exact rollback; review source ledger manually for compensation.",
    }


def _review_summary(run_dir: Path) -> dict:
    run_dir = run_dir.resolve()
    run = _load_json(run_dir / "run.json")
    checkpoints = []
    recommended_commands = []
    blockers: list[dict] = []
    for item in timeline_summary(run_dir)["checkpoints"]:
        cp_dir = run_dir / "checkpoints" / item["id"]
        changes = _checkpoint_changes(cp_dir)
        rollback_target = {
            "run_id": run["run_id"],
            "checkpoint_id": item["id"],
            "plan_command": f"safeloop rollback plan {run_dir} {run['run_id']} {item['id']}",
            "apply_command": f"safeloop rollback apply {run_dir} {run['run_id']} {item['id']}",
        }
        if item.get("blocked_reason"):
            blockers.append({"checkpoint_id": item["id"], "reason": item["blocked_reason"]})
        checkpoints.append({
            "checkpoint_id": item["id"],
            "parent_checkpoint_id": item.get("parent"),
            "timestamp": item.get("timestamp"),
            "rollback_support_status": item.get("undo_status", "unknown"),
            "digest_status": item.get("digest_status"),
            "blocker": item.get("blocked_reason"),
            "changed_files": changes,
            "rollback_target": rollback_target,
        })
        recommended_commands.extend([
            {"checkpoint_id": item["id"], "purpose": "preflight rollback plan", "command": rollback_target["plan_command"]},
            {"checkpoint_id": item["id"], "purpose": "apply rollback after operator approval", "command": rollback_target["apply_command"]},
        ])
    recommended_commands.extend([
        {"checkpoint_id": "START", "purpose": "preflight rollback-to-start plan", "command": f"safeloop rollback plan {run_dir} {run['run_id']} --to-start"},
        {"checkpoint_id": "START", "purpose": "apply rollback-to-start after operator approval", "command": f"safeloop rollback apply {run_dir} {run['run_id']} --to-start"},
    ])
    summary = {
        "schema_version": "review-summary.v1",
        "run": {
            "run_id": run.get("run_id"),
            "task_id": run.get("task_id"),
            "status": run.get("status"),
            "exit_code": run.get("exit_code"),
            "checkpoint_count": run.get("checkpoint_count"),
        },
        "checkpoints": checkpoints,
        "rollback_targets": [c["rollback_target"] for c in checkpoints],
        "changed_files": {
            "created": sorted({p for c in checkpoints for p in c["changed_files"]["created"]}),
            "modified": sorted({p for c in checkpoints for p in c["changed_files"]["modified"]}),
            "deleted": sorted({p for c in checkpoints for p in c["changed_files"]["deleted"]}),
        },
        "side_effects": _side_effect_review(run_dir),
        "rollback_support_status": "blocked" if blockers else "review_required",
        "blockers": blockers,
        "recommended_commands": recommended_commands,
    }
    atomic_json(run_dir / "review-summary.json", summary)
    return summary


def _print_review(summary: dict) -> None:
    run = summary["run"]
    print("SafeLoop rollback review")
    print(f"run id: {run['run_id']}")
    print(f"task id: {run['task_id']}")
    print(f"status: {run['status']}")
    print(f"checkpoints: {run['checkpoint_count']}")
    print("checkpoints:")
    for cp in summary["checkpoints"]:
        print(f"  {cp['checkpoint_id']} parent={cp['parent_checkpoint_id']} rollback={cp['rollback_support_status']} blocker={cp['blocker']}")
        print("    changed files:")
        for kind, paths in cp["changed_files"].items():
            print(f"      {kind}: {', '.join(paths) if paths else '-'}")
    se = summary["side_effects"]
    print("side effects:")
    print(f"  support: {se['support_status']}")
    print(f"  compensation: {'compensation_required' if se['compensation_required'] else 'none_recorded'}")
    print(f"  exact rollback: {str(se['exact_rollback']).lower()}")
    print("recommended commands:")
    for cmd in summary["recommended_commands"]:
        print(f"  {cmd['command']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="safeloop")
    sub = parser.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("watch-run")
    w.add_argument("--task-id", required=True)
    w.add_argument("--repo", required=True)
    w.add_argument("--run-root")
    w.add_argument("--debounce-ms", type=int, default=750)
    w.add_argument("--max-interval-sec", type=int, default=0)
    w.add_argument("command", nargs=argparse.REMAINDER)
    watch = sub.add_parser("watch")
    watch.add_argument("--loop", action="store_true", help="Run local watchdog loop (0.0.4 alias for watch-run)")
    watch.add_argument("--task-id", required=True)
    watch.add_argument("--repo", required=True)
    watch.add_argument("--run-root")
    watch.add_argument("--debounce-ms", type=int, default=750)
    watch.add_argument("--max-interval-sec", type=int, default=0)
    watch.add_argument("command", nargs=argparse.REMAINDER)
    v = sub.add_parser("verify-artifacts")
    v.add_argument("run_dir")
    va = sub.add_parser("verify-anchor")
    va.add_argument("run_dir")
    ca = sub.add_parser("audit-control-plane-anchors")
    ca.add_argument("--db", required=True)
    ca.add_argument("--anchors", required=True)
    ca.add_argument("--output-dir", required=True)
    ca.add_argument("--artifact", action="append", default=[])
    ca.add_argument("--artifact-base")
    t = sub.add_parser("timeline")
    t.add_argument("run_dir")
    t.add_argument("--json", action="store_true")
    t.add_argument("--checkpoint")
    u = sub.add_parser("undo")
    u.add_argument("run_dir")
    u.add_argument("run_id")
    u.add_argument("checkpoint_id")
    g = u.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    r = sub.add_parser("review")
    r.add_argument("run_dir")
    r.add_argument("--json", action="store_true")
    rb = sub.add_parser("rollback")
    rb_sub = rb.add_subparsers(dest="rollback_cmd", required=True)
    rb_plan = rb_sub.add_parser("plan")
    rb_plan.add_argument("run_dir")
    rb_plan.add_argument("run_id")
    rb_plan.add_argument("checkpoint_id", nargs="?")
    rb_plan.add_argument("--to-start", action="store_true")
    rb_plan.add_argument("--from-checkpoint", dest="from_checkpoint")
    rb_plan.add_argument("--files", nargs="+", default=[])
    rb_plan.add_argument("--hunks", nargs="+", default=[])
    rb_apply = rb_sub.add_parser("apply")
    rb_apply.add_argument("run_dir")
    rb_apply.add_argument("run_id")
    rb_apply.add_argument("checkpoint_id", nargs="?")
    rb_apply.add_argument("--to-start", action="store_true")
    rb_apply.add_argument("--from-checkpoint", dest="from_checkpoint")
    rb_apply.add_argument("--files", nargs="+", default=[])
    rb_apply.add_argument("--hunks", nargs="+", default=[])
    args = parser.parse_args(argv)

    if args.cmd in {"watch-run", "watch"}:
        if args.cmd == "watch" and not args.loop:
            print("watch requires --loop", file=sys.stderr)
            return 2
        cmd = args.command[1:] if args.command and args.command[0] == "--" else args.command
        if not cmd:
            print("watch-run requires command after --", file=sys.stderr)
            return 2
        code, run_dir = watch_run(
            args.task_id,
            Path(args.repo),
            cmd,
            Path(args.run_root) if args.run_root else None,
            args.debounce_ms,
            args.max_interval_sec,
        )
        run_json = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        print(f"Run id: {run_json['run_id']}")
        print(f"Run dir: {run_dir}")
        print(f"Status: {run_json['status']}")
        print(f"Exit code: {run_json['exit_code']}")
        print(f"Checkpoints: {run_json['checkpoint_count']}")
        return code
    if args.cmd == "verify-artifacts":
        result = verify_run(Path(args.run_dir))
        print(json.dumps(result, indent=2))
        return 0 if result["status"] in {"valid", "warning"} else 1
    if args.cmd == "verify-anchor":
        result = verify_local_anchor(Path(args.run_dir))
        print(json.dumps(result, indent=2))
        return 0 if result["status"] == "valid" else 1
    if args.cmd == "audit-control-plane-anchors":
        result = audit_control_plane_anchors(
            Path(args.db),
            Path(args.anchors),
            output_dir=Path(args.output_dir),
            artifacts=[Path(p) for p in args.artifact],
            artifact_base=Path(args.artifact_base) if args.artifact_base else None,
        )
        print("SafeLoop control-plane tamper-evident local audit")
        print(json.dumps(result, indent=2))
        return 0 if result["status"] == "valid" else 1
    if args.cmd == "timeline":
        summary = timeline_summary(Path(args.run_dir))
        if args.checkpoint:
            summary["checkpoints"] = [c for c in summary["checkpoints"] if c["id"] == args.checkpoint]
        if args.json:
            print(json.dumps(summary, indent=2))
            return 0
        r = summary["run"]
        print("SafeLoop timeline — tamper-evident local artifacts")
        print(
            f"run id: {r['run_id']} task id: {r['task_id']} status: {r['status']} "
            f"exit code: {r['exit_code']} checkpoints: {r['checkpoint_count']} latest hash: {r['latest_event_hash']}"
        )
        print("checkpoints:")
        for c in summary["checkpoints"]:
            print(
                f"  {c['id']} parent={c['parent']} digest={c['digest_status']} undo={c['undo_status']} "
                f"blocked={c['blocked_reason']} side_effects={c['side_effect_status']} timestamp={c['timestamp']}"
            )
        return 0
    if args.cmd == "review":
        summary = _review_summary(Path(args.run_dir))
        if args.json:
            print(json.dumps(summary, indent=2, sort_keys=True))
        else:
            _print_review(summary)
        return 0
    if args.cmd == "rollback":
        run_dir = Path(args.run_dir)
        apply_mode = args.rollback_cmd == "apply"
        if args.hunks:
            selected = _normalize_selected_hunks(args.hunks)
            if apply_mode:
                plan_path = run_dir / "rollback-plan.json"
                mismatch = None
                plan = None
                if not plan_path.exists():
                    mismatch = "missing rollback-plan.json"
                else:
                    plan = _load_json(plan_path)
                    if plan.get("operation") != "rollback_selected_hunks" or plan.get("schema_version") != "rollback-plan.v1" or plan.get("run_id") != args.run_id or plan.get("mode") != "dry-run" or plan.get("preflight", {}).get("status") != "pass":
                        mismatch = "plan mismatch"
                    elif plan.get("selected_hunks") != selected:
                        mismatch = "selected_hunks mismatch"
                if mismatch:
                    artifact = {"schema_version": "rollback-result.v1", "operation": "rollback_selected_hunks", "run_id": args.run_id, "mode": "apply", "selected_hunks": selected, "status": "blocked", "preflight": {"status": "blocked"}, "blocked_reason": mismatch}
                    atomic_json(run_dir / "rollback-result.json", artifact)
                    digests = {"rollback-result.json": sha_file(run_dir / "rollback-result.json")}
                    if plan_path.exists(): digests["rollback-plan.json"] = sha_file(plan_path)
                    _append_rollback_event(run_dir, "rollback_blocked", {"operation": "rollback_selected_hunks", "reason": mismatch, "selected_hunks": selected, "artifact_digests": digests})
                    print("Rollback apply blocked"); print(f"blocked reason: {mismatch}"); return 1
            artifact = _selected_hunk_rollback(run_dir, args.run_id, selected, apply=apply_mode)
            if apply_mode:
                event_type = "rollback_applied" if artifact["status"] == "applied" else "rollback_blocked"
                _append_rollback_event(run_dir, event_type, {"operation": "rollback_selected_hunks", "rollback_status": artifact["status"], "selected_hunks": selected, "artifact_digests": {"rollback-plan.json": sha_file(run_dir / "rollback-plan.json"), "rollback-result.json": sha_file(run_dir / "rollback-result.json")}})
                print("Rollback apply complete" if artifact["status"] == "applied" else "Rollback apply blocked")
                print(f"rollback-result.json: {run_dir / 'rollback-result.json'}")
            else:
                _append_rollback_event(run_dir, "rollback_plan_created", {"operation": "rollback_selected_hunks", "selected_hunks": selected, "artifact_digests": {"rollback-plan.json": sha_file(run_dir / "rollback-plan.json")}})
                print("Rollback plan: PASS" if artifact["preflight"]["status"] == "pass" else "Rollback plan: BLOCKED")
                print(f"rollback-plan.json: {run_dir / 'rollback-plan.json'}")
            print("SafeLoop rollback operator summary")
            print(f"run id: {artifact['run_id']}"); print(f"mode: {artifact['mode']}"); print(f"file counts: {artifact['file_counts']}")
            if artifact.get("blocked_reason"): print(f"blocked reason: {artifact['blocked_reason']}")
            return 1 if artifact["preflight"]["status"] == "blocked" and apply_mode else 0
        if args.files:
            selected = _normalize_selected_files(args.files)
            checkpoint_id = args.from_checkpoint or args.checkpoint_id
            if apply_mode:
                plan_path = run_dir / "rollback-plan.json"
                if not plan_path.exists():
                    artifact = {"schema_version": "rollback-result.v1", "operation": "rollback_selected_files", "run_id": args.run_id, "mode": "apply", "selected_files": selected, "target": _target_from_args(checkpoint_id), "status": "blocked", "preflight": {"status": "blocked"}, "blocked_reason": "missing rollback-plan.json"}
                    atomic_json(run_dir / "rollback-result.json", artifact)
                    _append_rollback_event(run_dir, "rollback_blocked", {"operation": "rollback_selected_files", "target": artifact["target"], "reason": artifact["blocked_reason"], "artifact_digests": {"rollback-result.json": sha_file(run_dir / "rollback-result.json")}})
                    print("Rollback apply blocked")
                    print(f"blocked reason: {artifact['blocked_reason']}")
                    return 1
                plan = _load_json(plan_path)
                expected_target = _target_from_args(checkpoint_id)
                mismatch = None
                if plan.get("operation") != "rollback_selected_files" or plan.get("schema_version") != "rollback-plan.v1" or plan.get("run_id") != args.run_id or plan.get("mode") != "dry-run" or plan.get("preflight", {}).get("status") != "pass":
                    mismatch = "plan mismatch"
                elif plan.get("selected_files") != selected:
                    mismatch = "selected_files mismatch"
                elif plan.get("target") != expected_target:
                    mismatch = "target mismatch"
                else:
                    for rel, expected_hash in plan.get("rollback_source_hashes", {}).items():
                        p = _safe_repo_path(Path(_load_json(run_dir / "run.json")["repo"]), rel)
                        actual = sha_file(p) if p.exists() else None
                        if actual != expected_hash:
                            mismatch = "current_hash_mismatch"
                            break
                if mismatch:
                    artifact = {"schema_version": "rollback-result.v1", "operation": "rollback_selected_files", "run_id": args.run_id, "mode": "apply", "selected_files": selected, "target": expected_target, "status": "blocked", "preflight": {"status": "blocked"}, "blocked_reason": mismatch}
                    atomic_json(run_dir / "rollback-result.json", artifact)
                    _append_rollback_event(run_dir, "rollback_blocked", {"operation": "rollback_selected_files", "target": expected_target, "reason": mismatch, "artifact_digests": {"rollback-plan.json": sha_file(plan_path), "rollback-result.json": sha_file(run_dir / "rollback-result.json")}})
                    print("Rollback apply blocked")
                    print(f"blocked reason: {mismatch}")
                    return 1
            artifact = _selected_rollback(run_dir, args.run_id, selected, checkpoint_id=checkpoint_id, apply=apply_mode)
            if apply_mode:
                event_type = "rollback_applied" if artifact["status"] == "applied" else "rollback_blocked"
                _append_rollback_event(run_dir, event_type, {"operation": "rollback_selected_files", "rollback_status": artifact["status"], "target": artifact["target"], "selected_files": selected, "artifact_digests": {"rollback-plan.json": sha_file(run_dir / "rollback-plan.json"), "rollback-result.json": sha_file(run_dir / "rollback-result.json")}})
                print("Rollback apply complete" if artifact["status"] == "applied" else "Rollback apply blocked")
                print(f"rollback-result.json: {run_dir / 'rollback-result.json'}")
            else:
                _append_rollback_event(run_dir, "rollback_plan_created", {"operation": "rollback_selected_files", "target": artifact["target"], "selected_files": selected, "artifact_digests": {"rollback-plan.json": sha_file(run_dir / "rollback-plan.json")}})
                print("Rollback plan: PASS" if artifact["preflight"]["status"] == "pass" else "Rollback plan: BLOCKED")
                print(f"rollback-plan.json: {run_dir / 'rollback-plan.json'}")
            print("SafeLoop rollback operator summary")
            print(f"run id: {artifact['run_id']}")
            print(f"mode: {artifact['mode']}")
            print(f"file counts: {artifact['file_counts']}")
            print(f"external side effects: {artifact['external_side_effects']['classification']}")
            if artifact.get("blocked_reason"):
                print(f"blocked reason: {artifact['blocked_reason']}")
            return 1 if artifact["preflight"]["status"] == "blocked" and apply_mode else 0
        if args.to_start:
            if apply_mode:
                ok, reason, plan = _require_matching_rollback_plan(run_dir, args.run_id, checkpoint_id=None, to_start=True)
                if not ok:
                    artifact = _block_rollback_apply(run_dir, args.run_id, checkpoint_id=None, to_start=True, reason=reason or "plan mismatch", plan=plan)
                    atomic_json(run_dir / "rollback-result.json", artifact)
                    print("Rollback apply blocked")
                    print(f"blocked reason: {artifact['blocked_reason']}")
                    print(f"rollback-result.json: {run_dir / 'rollback-result.json'}")
                    return 1
            artifact = rollback_to_start(run_dir, args.run_id, apply=apply_mode)
            if apply_mode:
                if artifact["status"] == "applied":
                    event_type = "rollback_applied"
                else:
                    atomic_json(run_dir / "rollback-result.json", artifact)
                    event_type = "rollback_blocked"
                _append_rollback_event(run_dir, event_type, {"operation": "rollback_to_start", "rollback_status": artifact["status"], "target": {"type": "start"}, "artifact_digests": {"rollback-plan.json": sha_file(run_dir / "rollback-plan.json"), "rollback-result.json": sha_file(run_dir / "rollback-result.json")}})
                print("Rollback apply complete" if artifact["status"] == "applied" else "Rollback apply blocked")
                print(f"rollback-result.json: {run_dir / 'rollback-result.json'}")
            else:
                _append_rollback_event(run_dir, "rollback_plan_created", {"operation": "rollback_to_start", "target": {"type": "start"}, "artifact_digests": {"rollback-plan.json": sha_file(run_dir / "rollback-plan.json")}})
                print("Rollback plan: PASS" if artifact["preflight"]["status"] == "pass" else "Rollback plan: BLOCKED")
                print(f"rollback-plan.json: {run_dir / 'rollback-plan.json'}")
            print("SafeLoop rollback operator summary")
            print(f"run id: {artifact['run_id']}")
            print("checkpoint id: START")
            print(f"mode: {artifact['mode']}")
            print(f"file counts: {artifact['file_counts']}")
            print(f"external side effects: {artifact['external_side_effects']['classification']}")
            if artifact.get("blocked_reason"):
                print(f"blocked reason: {artifact['blocked_reason']}")
            return 1 if artifact["preflight"]["status"] == "blocked" and apply_mode else 0
        if not args.checkpoint_id:
            print("rollback requires checkpoint_id or --to-start", file=sys.stderr)
            return 2
        if apply_mode:
            ok, reason, plan = _require_matching_rollback_plan(run_dir, args.run_id, checkpoint_id=args.checkpoint_id, to_start=False)
            if not ok:
                artifact = _block_rollback_apply(run_dir, args.run_id, checkpoint_id=args.checkpoint_id, to_start=False, reason=reason or "plan mismatch", plan=plan)
                atomic_json(run_dir / "checkpoints" / args.checkpoint_id / "rollback-result.json", artifact)
                print("Rollback apply blocked")
                print(f"blocked reason: {artifact['blocked_reason']}")
                print(f"rollback-result.json: {run_dir / 'checkpoints' / args.checkpoint_id / 'rollback-result.json'}")
                return 1
        res = undo(run_dir, args.run_id, args.checkpoint_id, apply=apply_mode)
        artifact = _rollback_artifact(run_dir, args.checkpoint_id, res, apply=apply_mode)
        if apply_mode:
            atomic_json(run_dir / "checkpoints" / args.checkpoint_id / "rollback-result.json", artifact)
            event_type = "rollback_applied" if artifact["status"] == "applied" else "rollback_blocked"
            _append_rollback_event(run_dir, event_type, {"operation": "rollback_to_checkpoint", "checkpoint_id": args.checkpoint_id, "rollback_status": artifact["status"], "target": _target_selection(artifact), "artifact_digests": {"rollback-plan.json": sha_file(run_dir / "rollback-plan.json"), "rollback-result.json": sha_file(run_dir / "checkpoints" / args.checkpoint_id / "rollback-result.json")}})
            print("Rollback apply complete" if artifact["status"] == "applied" else "Rollback apply blocked")
            print(f"rollback-result.json: {run_dir / 'checkpoints' / args.checkpoint_id / 'rollback-result.json'}")
        else:
            atomic_json(run_dir / "rollback-plan.json", artifact)
            _append_rollback_event(run_dir, "rollback_plan_created", {"operation": "rollback_to_checkpoint", "target": _target_selection(artifact), "artifact_digests": {"rollback-plan.json": sha_file(run_dir / "rollback-plan.json")}})
            print("Rollback plan: PASS" if artifact["preflight"]["status"] == "pass" else "Rollback plan: BLOCKED")
            print(f"rollback-plan.json: {run_dir / 'rollback-plan.json'}")
        print("SafeLoop rollback operator summary")
        print(f"run id: {artifact['run_id']}")
        print(f"checkpoint id: {artifact['checkpoint_id']}")
        print(f"mode: {artifact['mode']}")
        print(f"file counts: {artifact['file_counts']}")
        print(f"external side effects: {artifact['external_side_effects']['classification']}")
        return 1 if artifact["preflight"]["status"] == "blocked" and apply_mode else 0
    if args.cmd == "undo":
        res = undo(Path(args.run_dir), args.run_id, args.checkpoint_id, apply=args.apply)
        blocked = res.get("status") == "blocked"
        if blocked:
            print("Undo preflight: BLOCKED")
        elif args.apply:
            print("Undo complete")
        else:
            print("Undo preflight: PASS")
        print("SafeLoop undo operator summary")
        print(f"run id: {res['run_id']}")
        print(f"checkpoint id: {res['checkpoint_id']}")
        print(f"mode: {res['mode']}")
        print(f"file counts: {res['file_counts']}")
        print(f"external side-effect status: {res['external_side_effect_status']}")
        if res.get("blocked_reason"):
            print(f"blocked reason: {res['blocked_reason']}")
        cp = Path(args.run_dir) / "checkpoints" / args.checkpoint_id
        print(f"undo-preflight.json: {cp / 'undo-preflight.json'}")
        print(f"rollback-plan.json: {Path(args.run_dir) / 'rollback-plan.json'}")
        print(f"undo-result.json: {cp / 'undo-result.json'}")
        return 1 if blocked and args.apply else 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
