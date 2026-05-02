from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from safeloop.agent_watchdog import timeline_summary, undo, verify_run, watch_run


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
    v = sub.add_parser("verify-artifacts")
    v.add_argument("run_dir")
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
    args = parser.parse_args(argv)

    if args.cmd == "watch-run":
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
