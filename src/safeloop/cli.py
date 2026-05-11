from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from safeloop.agent_watchdog import atomic_json, rollback_to_start, timeline_summary, undo, verify_run, watch_run
from safeloop.control_plane.anchor_audit import audit_control_plane_anchors
from safeloop.local_anchor import verify_local_anchor


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
    if args.cmd == "rollback":
        run_dir = Path(args.run_dir)
        apply_mode = args.rollback_cmd == "apply"
        if args.to_start:
            artifact = rollback_to_start(run_dir, args.run_id, apply=apply_mode)
            if apply_mode:
                print("Rollback apply complete" if artifact["status"] == "applied" else "Rollback apply blocked")
                print(f"rollback-result.json: {run_dir / 'rollback-result.json'}")
            else:
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
        res = undo(run_dir, args.run_id, args.checkpoint_id, apply=apply_mode)
        artifact = _rollback_artifact(run_dir, args.checkpoint_id, res, apply=apply_mode)
        if apply_mode:
            atomic_json(run_dir / "checkpoints" / args.checkpoint_id / "rollback-result.json", artifact)
            print("Rollback apply complete" if artifact["status"] == "applied" else "Rollback apply blocked")
            print(f"rollback-result.json: {run_dir / 'checkpoints' / args.checkpoint_id / 'rollback-result.json'}")
        else:
            atomic_json(run_dir / "rollback-plan.json", artifact)
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
