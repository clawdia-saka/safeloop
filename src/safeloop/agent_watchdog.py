"""SafeLoop 0.0.3 local agent watchdog artifacts."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Thread
from typing import Any, TextIO


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def atomic_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _is_gitignored(repo: Path, rel: Path) -> bool:
    repo = repo.resolve()
    try:
        result = subprocess.run(
            ["git", "check-ignore", "--quiet", str(rel)],
            cwd=repo,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False


def rel_files(repo: Path) -> list[Path]:
    repo = repo.resolve()
    out: list[Path] = []
    for p in repo.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(repo)
        parts = set(rel.parts)
        if ".git" in parts or ".safeloop" in parts or "__pycache__" in parts or "node_modules" in parts:
            continue
        if _is_gitignored(repo, rel):
            continue
        out.append(rel)
    return sorted(out)


def snapshot(repo: Path) -> dict[str, str]:
    return {str(r): sha_file(repo / r) for r in rel_files(repo)}


def snapshot_bytes(repo: Path) -> dict[str, bytes]:
    return {str(r): (repo / r).read_bytes() for r in rel_files(repo)}


def state_digest(snap: dict[str, str]) -> str:
    return sha_bytes(json.dumps(snap, sort_keys=True, separators=(",", ":")).encode())


def safe_task_slug(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", value):
        raise ValueError("task_id must match [A-Za-z0-9_.-]{1,80}")
    return value


def safe_checkpoint_id(value: str) -> str:
    if not re.fullmatch(r"cp-\d{4,}", value):
        raise ValueError("checkpoint_id must match cp-0001 style")
    return value


def safe_child_dir(root: Path, name: str) -> Path:
    if Path(name).is_absolute():
        raise ValueError("unsafe absolute child path")
    resolved_root = root.resolve()
    child = (resolved_root / name).resolve()
    if resolved_root not in child.parents:
        raise ValueError("unsafe child path")
    return child


def safe_child_file(root: Path, name: str) -> Path:
    path = safe_child_dir(root, name)
    if path.is_dir():
        raise ValueError("expected file path, got directory")
    return path


def _event_hash(event_without_hash: dict[str, Any]) -> str:
    return sha_bytes(json.dumps(event_without_hash, sort_keys=True, separators=(",", ":")).encode())


def append_event(timeline: Path, seq: int, typ: str, payload: dict[str, Any], prev: str | None) -> str:
    base = {
        "schema_version": "timeline-event.v1",
        "event_id": f"evt-{seq:04d}",
        "seq": seq,
        "type": typ,
        "timestamp": now(),
        "prev_event_hash": prev,
        "payload": payload,
    }
    base["event_hash"] = _event_hash(base)
    timeline.parent.mkdir(parents=True, exist_ok=True)
    with timeline.open("a", encoding="utf-8") as f:
        f.write(json.dumps(base, sort_keys=True) + "\n")
    return base["event_hash"]


def make_diff(before: dict[str, str], after: dict[str, str]) -> str:
    lines: list[str] = []
    for f in sorted(set(before) | set(after)):
        if f not in before:
            lines.append(f"A {f}\n")
        elif f not in after:
            lines.append(f"D {f}\n")
        elif before[f] != after[f]:
            lines.append(f"M {f}\n")
    return "".join(lines)


def _blob_path(cp: Path, digest: str) -> Path:
    return cp / "blobs" / digest.replace("sha256:", "sha256-")


def _capture_before_blobs(cp: Path, before_bytes: dict[str, bytes], files: list[str]) -> dict[str, str | None]:
    blobs: dict[str, str | None] = {}
    for name in files:
        data = before_bytes.get(name)
        if data is None:
            blobs[name] = None
            continue
        digest = sha_bytes(data)
        dst = _blob_path(cp, digest)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists():
            dst.write_bytes(data)
        blobs[name] = str(dst.relative_to(cp))
    return blobs


def create_checkpoint(
    run_dir: Path,
    repo: Path,
    cid: str,
    parent: str | None,
    before: dict[str, str],
    after: dict[str, str],
    before_bytes: dict[str, bytes],
) -> None:
    cp = run_dir / "checkpoints" / cid
    cp.mkdir(parents=True, exist_ok=True)
    created = [f for f in after if f not in before]
    modified = [f for f in after if f in before and before[f] != after[f]]
    deleted = [f for f in before if f not in after]
    before_blobs = _capture_before_blobs(cp, before_bytes, modified + deleted)
    checkpoint = {
        "schema_version": "checkpoint.v1",
        "checkpoint_id": cid,
        "checkpoint_seq": int(cid.removeprefix("cp-")),
        "allocated_by": "monotonic-run-local",
        "parent_checkpoint_id": parent,
        "previous_state_digest": state_digest(before),
        "current_state_digest": state_digest(after),
        "created_at": now(),
    }
    manifest = {"schema_version": "manifest.v1", "files": after}
    restore = {
        "schema_version": "restore-manifest.v2",
        "checkpoint_id": cid,
        "repo": str(repo),
        "created_files": created,
        "modified_files": modified,
        "deleted_files": deleted,
        "before_blobs": before_blobs,
        "after_hashes": {f: after[f] for f in created + modified if f in after},
        "before_hashes": {f: before[f] for f in modified + deleted if f in before},
    }
    atomic_json(cp / "manifest.json", manifest)
    atomic_text(cp / "diff.patch", make_diff(before, after))
    atomic_json(cp / "restore-manifest.json", restore)
    changed = len(created) + len(modified) + len(deleted)
    atomic_text(cp / "summary.md", f"# {cid}\n\nFiles changed: {changed}\n")
    checkpoint["artifact_digests"] = {
        name: sha_file(cp / name)
        for name in ["manifest.json", "diff.patch", "restore-manifest.json", "summary.md"]
    }
    atomic_json(cp / "checkpoint.json", checkpoint)


def _drain_pipe(pipe: TextIO | None, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as out:
        if pipe is None:
            return
        for chunk in iter(pipe.readline, ""):
            out.write(chunk)
            out.flush()


def watch_run(
    task_id: str,
    repo: Path,
    command: list[str],
    run_root: Path | None = None,
    debounce_ms: int = 750,
    max_interval_sec: int = 0,
) -> tuple[int, Path]:
    del max_interval_sec  # Reserved for post-RC interval checkpoints.
    repo = repo.resolve()
    safe_slug = safe_task_slug(task_id)
    run_id = "run-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + f"-{safe_slug}"
    run_root_path = (run_root or Path.home() / ".safeloop" / "runs")
    run_dir = safe_child_dir(run_root_path, run_id)
    run_dir.mkdir(parents=True)
    timeline = run_dir / "timeline.jsonl"
    timeline.touch()
    (run_dir / "side-effects.jsonl").touch()
    run = {
        "schema_version": "run.v1",
        "run_id": run_id,
        "task_id": task_id,
        "agent_id": None,
        "repo": str(repo),
        "command": command,
        "cwd": str(repo),
        "started_at": now(),
        "ended_at": None,
        "status": "running",
        "exit_code": None,
        "checkpoint_count": 0,
        "external_side_effect_count": 0,
        "latest_event_hash": None,
        "final_event_hash": None,
        "capture_status": "running",
    }
    atomic_json(run_dir / "run.json", run)
    prev = append_event(timeline, 1, "run_started", {"run_id": run_id, "task_id": task_id}, None)
    seq = 2

    last_snap = snapshot(repo)
    last_bytes = snapshot_bytes(repo)
    last_digest = state_digest(last_snap)
    checkpoint_count = 0
    parent: str | None = None

    proc = subprocess.Popen(
        command,
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    stdout_thread = Thread(target=_drain_pipe, args=(proc.stdout, run_dir / "command.stdout.txt"), daemon=True)
    stderr_thread = Thread(target=_drain_pipe, args=(proc.stderr, run_dir / "command.stderr.txt"), daemon=True)
    stdout_thread.start(); stderr_thread.start()

    debounce_sec = max(debounce_ms, 0) / 1000
    poll_sec = min(max(debounce_sec / 2, 0.02), 0.1)
    pending_snap: dict[str, str] | None = None
    pending_since: float | None = None

    def maybe_checkpoint(force: bool = False) -> None:
        nonlocal last_snap, last_bytes, last_digest, checkpoint_count, parent, prev, seq, pending_snap, pending_since
        current = snapshot(repo)
        digest = state_digest(current)
        if digest == last_digest:
            pending_snap = None; pending_since = None
            return
        now_mono = time.monotonic()
        if pending_snap != current:
            pending_snap = current; pending_since = now_mono
            if not force:
                return
        if not force and pending_since is not None and now_mono - pending_since < debounce_sec:
            return
        checkpoint_count += 1
        cid = f"cp-{checkpoint_count:04d}"
        create_checkpoint(run_dir, repo, cid, parent, last_snap, current, last_bytes)
        cp = run_dir / "checkpoints" / cid
        digests = {name: sha_file(cp / name) for name in ["checkpoint.json", "manifest.json", "diff.patch", "restore-manifest.json", "summary.md"]}
        prev = append_event(timeline, seq, "checkpoint_created", {"checkpoint_id": cid, "parent_checkpoint_id": parent, "artifact_digests": digests}, prev)
        seq += 1
        parent = cid
        last_snap = current
        last_bytes = snapshot_bytes(repo)
        last_digest = digest
        pending_snap = None; pending_since = None

    while proc.poll() is None:
        maybe_checkpoint(force=False)
        time.sleep(poll_sec)
    stdout_thread.join(timeout=2); stderr_thread.join(timeout=2)
    maybe_checkpoint(force=True)

    process_result = {"schema_version": "process-result.v1", "exit_code": proc.returncode, "completed_at": now()}
    atomic_json(run_dir / "process-result.json", process_result)
    prev = append_event(timeline, seq, "process_exited", {"exit_code": proc.returncode, "artifact_digests": {"process-result.json": sha_file(run_dir / "process-result.json")}}, prev)
    seq += 1
    status = "completed" if proc.returncode == 0 else "failed"
    prev = append_event(timeline, seq, "run_closed", {"status": status}, prev)
    run.update({"ended_at": now(), "status": status, "exit_code": proc.returncode, "checkpoint_count": checkpoint_count, "latest_event_hash": prev, "final_event_hash": prev, "capture_status": "complete"})
    atomic_json(run_dir / "run.json", run)
    return int(proc.returncode or 0), run_dir


def timeline_events(run_dir: Path) -> list[dict[str, Any]]:
    p = run_dir / "timeline.jsonl"
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def verify_run(run_dir: Path) -> dict[str, Any]:
    issues: list[str] = []
    warnings: list[str] = []
    prev = None
    checked: list[str] = []
    try:
        events = timeline_events(run_dir)
        expected_seq = 1
        for e in events:
            h = e.get("event_hash")
            without_hash = dict(e)
            without_hash.pop("event_hash", None)
            expected = _event_hash(without_hash)
            if e.get("seq") != expected_seq:
                issues.append(f"event sequence mismatch {e.get('event_id')}")
            expected_seq += 1
            if h != expected:
                issues.append(f"timeline hash mismatch {e.get('event_id')}")
            if e.get("prev_event_hash") != prev:
                issues.append(f"timeline prev hash mismatch {e.get('event_id')}")
            prev = h
            for name, dig in e.get("payload", {}).get("artifact_digests", {}).items():
                checkpoint_id = e.get("payload", {}).get("checkpoint_id", "")
                candidates = [run_dir / name]
                if checkpoint_id:
                    candidates.append(run_dir / "checkpoints" / checkpoint_id / name)
                path = next((c for c in candidates if c.exists()), None)
                if path is None:
                    issues.append(f"missing bound artifact {name}")
                else:
                    checked.append(str(path.relative_to(run_dir)))
                    if sha_file(path) != dig:
                        issues.append(f"digest mismatch {path.relative_to(run_dir)}")
        r = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        for required in ["command.stdout.txt", "command.stderr.txt", "process-result.json", "side-effects.jsonl"]:
            if not (run_dir / required).exists():
                issues.append(f"capture missing {required}")
        if r.get("capture_status") != "complete" and r.get("status") in {"completed", "failed"}:
            issues.append(f"invalid_partial finalization capture_status={r.get('capture_status')}")
        if r.get("final_event_hash") != prev:
            issues.append("run final hash mismatch")
        parent = None
        for cp in sorted((run_dir / "checkpoints").glob("cp-*")) if (run_dir / "checkpoints").exists() else []:
            cj = json.loads((cp / "checkpoint.json").read_text(encoding="utf-8"))
            if cj.get("parent_checkpoint_id") != parent:
                issues.append(f"invalid parent chain {cp.name}")
            parent = cp.name
            restore = json.loads((cp / "restore-manifest.json").read_text(encoding="utf-8"))
            for rel_blob in restore.get("before_blobs", {}).values():
                if rel_blob is not None and not safe_child_file(cp, rel_blob).exists():
                    issues.append(f"restore blob missing {cp.name}/{rel_blob}")
        if (run_dir / "side-effects.jsonl").exists() and not (run_dir / "side-effects.jsonl").read_text(encoding="utf-8").strip():
            pass
    except Exception as exc:
        issues.append(f"verification error: {exc}")
    status = "invalid" if issues else ("warning" if warnings else "valid")
    result = {"schema_version": "verify-artifacts-result.v1", "status": status, "issues": issues, "warnings": warnings, "checked_artifacts": checked, "latest_event_hash": prev, "verified_at": now(), "copy": "tamper-evident local artifacts"}
    atomic_json(run_dir / "verification" / "verify-artifacts-result.json", result)
    return result


def _safe_repo_path(repo: Path, rel: str) -> Path:
    path = (repo / rel).resolve()
    if repo.resolve() not in path.parents and path != repo.resolve():
        raise ValueError(f"unsafe restore path: {rel}")
    return path


def undo(run_dir: Path, run_id: str, checkpoint_id: str, apply: bool = False) -> dict[str, Any]:
    run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    if run.get("run_id") != run_id:
        raise ValueError(f"run_id mismatch: expected {run.get('run_id')}, got {run_id}")
    checkpoint_id = safe_checkpoint_id(checkpoint_id)
    checkpoints_root = (run_dir / "checkpoints").resolve()
    cp = safe_child_dir(checkpoints_root, checkpoint_id)
    restore = json.loads((cp / "restore-manifest.json").read_text(encoding="utf-8"))
    repo = Path(run["repo"])
    baseline = verify_run(run_dir)
    if baseline["status"] != "valid":
        raise ValueError("cannot run undo preflight: artifact verification is not valid")
    created = restore.get("created_files", [])
    modified = restore.get("modified_files", [])
    deleted = restore.get("deleted_files", [])
    blockers: list[dict[str, str]] = []
    for f, expected_hash in restore.get("after_hashes", {}).items():
        p = _safe_repo_path(repo, f)
        actual = sha_file(p) if p.exists() else None
        if actual != expected_hash:
            blockers.append({"file": f, "code": "current_hash_mismatch", "expected": expected_hash, "actual": str(actual)})
    for f in deleted:
        p = _safe_repo_path(repo, f)
        if p.exists():
            actual = sha_file(p) if p.is_file() else "non_file_exists"
            blockers.append({"file": f, "code": "deleted_path_recreated", "expected": "absent", "actual": str(actual)})
    status = "blocked" if blockers else "ok"
    pre = {"schema_version": "undo-preflight.v1", "run_id": run_id, "checkpoint_id": checkpoint_id, "mode": "apply" if apply else "dry-run", "status": status, "blockers": blockers, "file_counts": {"created": len(created), "modified": len(modified), "deleted": len(deleted)}, "external_side_effect_status": "not_tracked"}
    atomic_json(cp / "undo-preflight.json", pre)
    atomic_json(run_dir / "rollback-plan.json", pre)
    if blockers:
        return {**pre, "blocked_reason": blockers[0]["code"]}
    if apply:
        for f in created:
            p = _safe_repo_path(repo, f)
            if p.exists():
                p.unlink()
        for f in modified + deleted:
            blob = restore.get("before_blobs", {}).get(f)
            p = _safe_repo_path(repo, f)
            if blob is None:
                if p.exists():
                    p.unlink()
            else:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(safe_child_file(cp, blob).read_bytes())
        result = {**pre, "schema_version": "undo-result.v1", "status": "applied", "restored_file_count": len(created) + len(modified) + len(deleted), "undo_result_path": str(cp / "undo-result.json")}
        atomic_json(cp / "undo-result.json", result)
        evs = timeline_events(run_dir)
        prev = evs[-1]["event_hash"] if evs else None
        new_hash = append_event(run_dir / "timeline.jsonl", len(evs) + 1, "undo_completed", {"checkpoint_id": checkpoint_id, "undo_status": "applied", "artifact_digests": {"undo-result.json": sha_file(cp / "undo-result.json")}}, prev)
        run["latest_event_hash"] = new_hash
        run["final_event_hash"] = new_hash
        atomic_json(run_dir / "run.json", run)
        return result
    return pre


def timeline_summary(run_dir: Path) -> dict[str, Any]:
    run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    cps = []
    for cp in sorted((run_dir / "checkpoints").glob("cp-*")) if (run_dir / "checkpoints").exists() else []:
        cj = json.loads((cp / "checkpoint.json").read_text(encoding="utf-8"))
        undo_status = "undoable"
        blocked_reason = None
        if (cp / "undo-preflight.json").exists():
            pre = json.loads((cp / "undo-preflight.json").read_text(encoding="utf-8"))
            if pre.get("status") == "blocked":
                undo_status = "blocked"
                blocked_reason = pre.get("blockers", [{}])[0].get("code")
        if (cp / "undo-result.json").exists():
            undo_status = json.loads((cp / "undo-result.json").read_text(encoding="utf-8")).get("status", "unknown")
        cps.append({"id": cp.name, "parent": cj.get("parent_checkpoint_id"), "digest_status": "bound", "undo_status": undo_status, "blocked_reason": blocked_reason, "side_effect_status": "not_tracked", "timestamp": cj.get("created_at")})
    return {"run": run, "copy": "tamper-evident local artifacts", "checkpoints": cps, "latest_events": timeline_events(run_dir)[-5:]}
