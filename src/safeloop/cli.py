from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from safeloop.agent_watchdog import atomic_json, append_event, rollback_to_start, sha_file, timeline_events, timeline_summary, undo, verify_run, watch_run
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
    return {"type": "checkpoint", "checkpoint_id": artifact.get("checkpoint_id")}


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
    rb_apply = rb_sub.add_parser("apply")
    rb_apply.add_argument("run_dir")
    rb_apply.add_argument("run_id")
    rb_apply.add_argument("checkpoint_id", nargs="?")
    rb_apply.add_argument("--to-start", action="store_true")
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
