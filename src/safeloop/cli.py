from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from importlib.metadata import PackageNotFoundError, version
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
from safeloop.compensation import (
    CompensationResultValidationError,
    build_compensation_plan,
    compensation_section_for_rollback,
    create_compensation_result,
)
from safeloop.control_plane.anchor_audit import audit_control_plane_anchors
from safeloop.external_effects import ExternalEffectValidationError, record_external_effect
from safeloop.html_artifacts import write_docs_packet_html, write_markdown_doc_html, write_readiness_html
from safeloop.local_anchor import create_local_anchor, verify_local_anchor
from safeloop.operator_packet import write_operator_packet_v2
from safeloop.operator_packet_manifest import (
    validate_operator_packet_manifest_packet_path,
    verify_operator_packet_manifest,
    write_operator_packet_manifest,
)
from safeloop.policy_check import PolicyCheckError, build_policy_rollback_suggestion, run_policy_check
from safeloop.rollback_groups import build_rollback_groups, print_rollback_groups
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


def _run_checked(cmd: list[str], *, cwd: Path) -> None:
    subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)


def _status_from_checks(checks: dict[str, dict]) -> str:
    statuses = {check["status"] for check in checks.values()}
    if "error" in statuses:
        return "errors"
    if "warn" in statuses:
        return "warnings"
    return "ok"


def _doctor_report(repo: Path) -> dict:
    repo = repo.resolve()
    checks: dict[str, dict] = {}
    checks["python"] = {
        "status": "ok" if sys.version_info >= (3, 11) else "error",
        "message": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
    }
    try:
        package_version = version("safeloop")
    except PackageNotFoundError:
        package_version = "0.2.0"
    checks["package"] = {"status": "ok", "message": f"safeloop {package_version}"}
    checks["git"] = {
        "status": "ok" if shutil.which("git") else "error",
        "message": shutil.which("git") or "git not found on PATH",
    }
    checks["repo"] = {
        "status": "ok" if (repo / ".git").exists() else "warn",
        "message": str(repo) if (repo / ".git").exists() else f"{repo} is not a git checkout",
    }
    cli_commands = ["demo", "doctor", "init", "watch-run", "operator-packet", "operator-packet-verify", "rollback"]
    checks["cli"] = {"status": "ok", "commands": cli_commands, "message": "required CLI surfaces are installed"}
    ci = repo / ".github" / "workflows" / "ci.yml"
    if ci.exists():
        ci_text = ci.read_text(encoding="utf-8")
        packet_markers = [
            "safeloop demo --output-dir .safeloop/ci-demo --json",
            "safeloop operator-packet-verify \"$RUN_DIR\"",
            "actions/upload-artifact@v4",
            "safeloop-packet-demo",
        ]
        missing = [marker for marker in packet_markers if marker not in ci_text]
        checks["github_action_packet_upload"] = {
            "status": "ok" if not missing else "warn",
            "message": "CI generates, verifies, and uploads SafeLoop demo packet artifacts" if not missing else "CI packet upload markers missing: " + ", ".join(missing),
            "artifact_name": "safeloop-packet-demo",
            "verifier_command": "safeloop operator-packet-verify",
        }
    else:
        checks["github_action_packet_upload"] = {
            "status": "warn",
            "message": "missing .github/workflows/ci.yml",
            "artifact_name": "safeloop-packet-demo",
            "verifier_command": "safeloop operator-packet-verify",
        }
    return {
        "schema_version": "safeloop.doctor.v1",
        "status": _status_from_checks(checks),
        "repo": str(repo),
        "checks": checks,
    }


def _print_doctor(report: dict) -> None:
    print("SafeLoop doctor")
    print(f"status: {report['status']}")
    for name, check in report["checks"].items():
        status = check["status"]
        message = check.get("message", "")
        print(f"[{status}] {name}: {message}")


def _codex_agent_instructions() -> str:
    return """# SafeLoop Codex Agent Notes

Use SafeLoop for local evidence packets around long-running or risky agent work.

- Run `safeloop doctor` before relying on packet upload or local rollback evidence.
- Run `safeloop demo` to produce a local sample run with `operator-packet-v2.md` and `operator-packet-manifest.json`.
- For real work, wrap commands with `safeloop watch-run --task-id <task> --repo "$PWD" -- <command>`.
- Generate review evidence with `safeloop operator-packet RUN_DIR --write-manifest`.
- Treat actions outside the local repo as compensation/manual-review only; do not claim exact rollback for external systems.
"""


def _init_agent(repo: Path, *, agent: str, force: bool) -> dict:
    agent = agent.lower()
    if agent != "codex":
        raise ValueError("supported agents: codex")
    repo = repo.resolve()
    safeloop_dir = repo / ".safeloop"
    config_path = safeloop_dir / "config.json"
    instructions_path = safeloop_dir / "AGENTS.codex.md"
    config = {
        "schema_version": "safeloop.init.v1",
        "agent": "codex",
        "packet_dir": ".safeloop/packets",
        "recommended_commands": [
            "safeloop doctor",
            "safeloop demo",
            "safeloop operator-packet RUN_DIR --write-manifest",
        ],
        "boundaries": {
            "local_file_rollback": "covered artifacts only",
            "external_actions": "manual_review_or_compensation",
            "exact_external_rollback": False,
        },
    }
    outputs = {
        config_path: json.dumps(config, indent=2, sort_keys=True) + "\n",
        instructions_path: _codex_agent_instructions(),
    }
    written: list[str] = []
    unchanged: list[str] = []
    conflicts: list[str] = []
    safeloop_dir.mkdir(parents=True, exist_ok=True)
    for path, content in outputs.items():
        if path.exists():
            existing = path.read_text(encoding="utf-8")
            if existing == content:
                unchanged.append(str(path))
                continue
            if not force:
                conflicts.append(str(path))
                continue
        path.write_text(content, encoding="utf-8")
        written.append(str(path))
    if conflicts:
        raise FileExistsError("would overwrite existing SafeLoop init file(s): " + ", ".join(conflicts))
    (safeloop_dir / "packets").mkdir(parents=True, exist_ok=True)
    return {
        "schema_version": "safeloop.init-result.v1",
        "status": "ok",
        "agent": agent,
        "repo": str(repo),
        "written": written,
        "unchanged": unchanged,
        "packet_dir": str(safeloop_dir / "packets"),
    }


def _prepare_demo_root(output_dir: str | None, *, force: bool) -> Path:
    root = Path(output_dir).resolve() if output_dir else Path(tempfile.mkdtemp(prefix="safeloop-demo-")).resolve()
    demo_paths = [
        root / "repo",
        root / "runs",
        root / "fake-external-service.log",
        root / "agent.py",
        root / "demo-summary.json",
    ]
    existing = [path for path in demo_paths if path.exists()]
    if existing and not force:
        raise FileExistsError("demo output already exists; pass --force or choose a new --output-dir: " + ", ".join(str(p) for p in existing))
    if force:
        for path in existing:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _write_demo_v1_packet(run_dir: Path, demo_repo: Path, external_log: Path, run_id: str, checkpoint_id: str) -> Path:
    packet = run_dir / "operator-packet.md"
    packet.write_text(
        "\n".join(
            [
                "# SafeLoop demo operator packet",
                "",
                f"Run directory: {run_dir}",
                f"Repository: {demo_repo}",
                f"Local change evidence: {run_dir / 'checkpoints' / checkpoint_id / 'diff.patch'}",
                f"Artifact verification: {run_dir / 'verification' / 'verify-artifacts-result.json'}",
                f"Rollback plan: {run_dir / 'rollback-plan.json'}",
                f"External evidence: {external_log}",
                "",
                "Decision boundary:",
                "- Covered local file rollback: service.md was rolled back from verified local artifacts.",
                "- External action: fake ticket remains manual-review/compensation territory.",
                f"- Exact rollback is not claimed for anything outside {demo_repo}.",
                "",
                "Rollback command:",
                f"safeloop rollback apply {run_dir} {run_id} {checkpoint_id} --files service.md",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return packet


def _run_demo(*, output_dir: str | None, force: bool) -> dict:
    root = _prepare_demo_root(output_dir, force=force)
    demo_repo = root / "repo"
    run_root = root / "runs"
    external_log = root / "fake-external-service.log"
    demo_repo.mkdir(parents=True)
    run_root.mkdir(parents=True)
    _run_checked(["git", "init", "-q"], cwd=demo_repo)
    _run_checked(["git", "config", "user.email", "demo@example.test"], cwd=demo_repo)
    _run_checked(["git", "config", "user.name", "demo"], cwd=demo_repo)
    (demo_repo / "service.md").write_text("status: stable\n", encoding="utf-8")
    _run_checked(["git", "add", "service.md"], cwd=demo_repo)
    _run_checked(["git", "commit", "-q", "-m", "init"], cwd=demo_repo)
    agent = root / "agent.py"
    agent.write_text(
        "\n".join(
            [
                "from pathlib import Path",
                "import sys",
                "",
                "external_log = Path(sys.argv[1])",
                "Path('service.md').write_text(",
                "    'status: changed by safeloop demo\\n'",
                "    'handoff: operator must review external-service evidence\\n',",
                "    encoding='utf-8',",
                ")",
                "external_log.write_text(",
                "    'fake-ticket: created outside repo; external_review_required; exact_rollback=false\\n',",
                "    encoding='utf-8',",
                ")",
                "print('agent changed service.md and simulated an external ticket')",
                "",
            ]
        ),
        encoding="utf-8",
    )
    code, run_dir = watch_run(
        "demo",
        demo_repo,
        [sys.executable, str(agent), str(external_log)],
        run_root,
        debounce_ms=250,
        max_interval_sec=0,
    )
    if code != 0:
        raise RuntimeError(f"demo agent exited with code {code}; see {run_dir}")
    run = _load_json(run_dir / "run.json")
    checkpoints = sorted((run_dir / "checkpoints").glob("cp-*"))
    if not checkpoints:
        raise RuntimeError(f"demo did not create a checkpoint: {run_dir}")
    checkpoint_id = checkpoints[-1].name
    verification = verify_run(run_dir)
    if verification["status"] not in {"valid", "warning"}:
        raise RuntimeError(f"demo artifact verification failed: {verification['status']}")
    _review_summary(run_dir)
    plan = _selected_rollback(run_dir, run["run_id"], ["service.md"], checkpoint_id=checkpoint_id, apply=False)
    plan["compensation"] = compensation_section_for_rollback(run_dir, action_id=None, include_compensation=False)
    atomic_json(run_dir / "rollback-plan.json", plan)
    _append_rollback_event(
        run_dir,
        "rollback_plan_created",
        {
            "operation": "rollback_selected_files",
            "target": plan["target"],
            "selected_files": ["service.md"],
            "artifact_digests": {"rollback-plan.json": sha_file(run_dir / "rollback-plan.json")},
        },
    )
    result = _selected_rollback(run_dir, run["run_id"], ["service.md"], checkpoint_id=checkpoint_id, apply=True)
    _append_rollback_event(
        run_dir,
        "rollback_applied" if result["status"] == "applied" else "rollback_blocked",
        {
            "operation": "rollback_selected_files",
            "rollback_status": result["status"],
            "target": result["target"],
            "selected_files": ["service.md"],
            "artifact_digests": {
                "rollback-plan.json": sha_file(run_dir / "rollback-plan.json"),
                "rollback-result.json": sha_file(run_dir / "rollback-result.json"),
            },
        },
    )
    v1_packet = _write_demo_v1_packet(run_dir, demo_repo, external_log, run["run_id"], checkpoint_id)
    v2_packet = write_operator_packet_v2(
        run_dir,
        output_path=run_dir / "operator-packet-v2.md",
        external_evidence=[str(external_log)],
        compensation_adapter="manual",
    )
    manifest = write_operator_packet_manifest(run_dir, v2_packet)
    summary = {
        "schema_version": "safeloop.demo.v1",
        "status": "ok",
        "workspace": str(root),
        "repo": str(demo_repo),
        "run_dir": str(run_dir),
        "run_id": run["run_id"],
        "checkpoint_id": checkpoint_id,
        "verification_status": verification["status"],
        "rollback_status": result["status"],
        "operator_packet": str(v2_packet),
        "operator_packet_v1": str(v1_packet),
        "operator_packet_manifest": str(run_dir / "operator-packet-manifest.json"),
        "manifest_status": manifest["verification"]["status"],
        "demo_summary": str(root / "demo-summary.json"),
        "external_effects": {
            "evidence": str(external_log),
            "exact_rollback": False,
            "status": "manual_review_required",
        },
    }
    atomic_json(root / "demo-summary.json", summary)
    return summary


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


def _expected_hunk_after_lines(cp: Path, hunk: dict) -> list[str] | None:
    """Return the exact post-change lines for one unified-diff hunk.

    Hunk manifests intentionally record the whole-file before/after hashes, but
    action rollback must preserve unrelated later edits in the same file.  Use
    the checkpoint patch to validate only the selected hunk range.
    """
    patch_path = cp / "changes.patch"
    if not patch_path.exists():
        patch_path = cp / "diff.patch"
    if not patch_path.exists():
        return None
    header = f"@@ -{int(hunk['old_start'])}"
    new_count = int(hunk["new_lines"])
    in_file = False
    for raw in patch_path.read_text(encoding="utf-8").splitlines(keepends=True):
        if raw.startswith("diff --git "):
            in_file = raw.rstrip("\n").endswith(f" b/{hunk['path']}")
            continue
        if raw.startswith("+++ "):
            in_file = raw.rstrip("\n") in {f"+++ b/{hunk['path']}", "+++ /dev/null"}
            continue
        if not in_file or not raw.startswith("@@ "):
            continue
        if not raw.startswith(header) or f" +{int(hunk['new_start'])}" not in raw:
            continue
        after: list[str] = []
        # Continue from the line after this header by re-reading with an index.
        lines = patch_path.read_text(encoding="utf-8").splitlines(keepends=True)
        start_idx = lines.index(raw) + 1
        for body in lines[start_idx:]:
            if body.startswith("@@ ") or body.startswith("diff --git "):
                break
            if body.startswith(" ") or body.startswith("+"):
                after.append(body[1:])
            if len(after) == new_count:
                return after
        return after if len(after) == new_count else None
    return None


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
        expected_after = _expected_hunk_after_lines(cp, h)
        whole_file_matches = bool(h.get("after_text_hash")) and _sha_bytes_local(p.read_bytes()) == h.get("after_text_hash")
        if not whole_file_matches and (expected_after is None or current[ns - 1 : ns - 1 + nl] != expected_after):
            blockers.append({"hunk_id": h["hunk_id"], "file": rel, "code": "hunk_context_mismatch", "expected": "selected hunk post-change lines", "actual": "current hunk range differs"}); continue
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
    target = _target_from_args(checkpoint_id)
    if checkpoint_id:
        cp_dir = run_dir / "checkpoints" / checkpoint_id
        restore = _load_json(cp_dir / "restore-manifest.json")
        changed = set(restore.get("created_files", []) + restore.get("modified_files", []) + restore.get("deleted_files", []))
        source_hashes = {f: restore.get("after_hashes", {}).get(f) for f in selected}
        restore_blobs = {f: b for f, b in restore.get("before_blobs", {}).items() if f in selected}
        restore_root = cp_dir
        created = sorted(f for f in selected if f in restore.get("created_files", []))
        modified = sorted(f for f in selected if f in restore.get("modified_files", []))
        deleted = sorted(f for f in selected if f in restore.get("deleted_files", []))
    else:
        base_dir = run_dir / "baseline"
        manifest = _load_json(base_dir / "manifest.json")
        before = manifest.get("files", {})
        current = snapshot(repo)
        changed = {f for f in current if f not in before} | {f for f in current if f in before and current[f] != before[f]} | {f for f in before if f not in current}
        source_hashes = {f: current.get(f) for f in selected}
        restore_blobs = {f: b for f, b in manifest.get("before_blobs", {}).items() if f in selected}
        restore_root = base_dir
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


def _action_group(run_dir: Path, run_id: str, action_id: str) -> dict:
    groups = build_rollback_groups(run_dir)
    if groups.get("run", {}).get("run_id") != run_id:
        raise ValueError(f"run_id mismatch: expected {groups.get('run', {}).get('run_id')}, got {run_id}")
    for group in groups.get("groups", []):
        if group.get("action_id") == action_id:
            return group
    raise ValueError(f"action not found: {action_id}")


def _action_rollback(run_dir: Path, run_id: str, action_id: str, *, apply: bool) -> dict:
    group = _action_group(run_dir, run_id, action_id)
    files = sorted(set(str(f) for f in group.get("files", []) if f))
    hunks = [h for h in group.get("hunks", []) if h.get("hunk_id")]
    hunk_ids = [str(h["hunk_id"]) for h in hunks]
    blockers: list[dict[str, str]] = []
    hunk_files = sorted({str(h.get("path")) for h in hunks if h.get("path")})
    mode = "hunk" if hunk_ids and (not files or set(hunk_files) == set(files)) else "file"
    if not hunk_ids and not files:
        blockers.append({"action_id": action_id, "code": "action_has_no_local_changes", "expected": "files or hunks", "actual": "none"})
    if mode == "hunk":
        for h in hunks:
            if h.get("binary") or h.get("undo_supported") is False or h.get("unsupported_reason"):
                blockers.append({"action_id": action_id, "hunk_id": str(h.get("hunk_id")), "file": str(h.get("path")), "code": str(h.get("unsupported_reason") or "unsupported_hunk"), "expected": "undo_supported hunk", "actual": "unsupported"})
    selected = hunk_ids if mode == "hunk" else files
    if not blockers:
        inner = _selected_hunk_rollback(run_dir, run_id, selected, apply=apply) if mode == "hunk" else _selected_rollback(run_dir, run_id, selected, checkpoint_id=None, apply=apply)
        blockers = list(inner.get("blockers", []))
    else:
        inner = {"status": "blocked", "preflight": {"status": "blocked"}, "file_counts": {"created": 0, "modified": len(files), "deleted": 0}}
    status = "blocked" if blockers else ("applied" if apply else "ok")
    side_effects = group.get("external_side_effects") or {"classification": "manual_review", "exact_rollback": False}
    side_effects = {**side_effects, "manual_review": True, "exact_rollback": False, "compensation_plan_required": bool((group.get("side_effects") or {}).get("count"))}
    artifact = {"schema_version": "rollback-result.v1" if apply else "rollback-plan.v1", "operation": "rollback_action", "run_id": run_id, "mode": "apply" if apply else "dry-run", "action_id": action_id, "action": {"name": group.get("title") or action_id, "intent": group.get("intent") or group.get("title") or action_id}, "rollback_mode": mode, "selected_hunks": hunk_ids if mode == "hunk" else [], "selected_files": files, "affected_files": files, "hunks": hunks, "checkpoints": group.get("checkpoints", []), "side_effects": group.get("side_effects", {"count": 0, "types": []}), "external_side_effects": side_effects, "status": status, "review_required": True, "blockers": blockers, "dependency_warnings": group.get("dependency_warnings", []) or inner.get("dependency_warnings", []), "preflight": {"status": "blocked" if blockers else "pass"}, "file_counts": inner.get("file_counts", {"created": 0, "modified": len(files), "deleted": 0}), "apply_command": f"safeloop rollback apply {run_dir} {run_id} --action {action_id}"}
    if apply:
        artifact["inner_result"] = {k: inner.get(k) for k in ("operation", "status", "restored_hunk_count", "restored_file_count") if k in inner}
        artifact["verification"] = verify_run(run_dir, check_source_evidence=False)
    if blockers:
        artifact["blocked_reason"] = blockers[0].get("code")
    atomic_json(run_dir / ("rollback-result.json" if apply else "rollback-plan.json"), artifact)
    return artifact


def _require_matching_action_plan(run_dir: Path, run_id: str, action_id: str) -> tuple[bool, str | None, dict | None]:
    plan_path = run_dir / "rollback-plan.json"
    if not plan_path.exists():
        return False, "missing rollback-plan.json", None
    try:
        plan = _load_json(plan_path)
    except (OSError, json.JSONDecodeError):
        return False, "malformed rollback-plan.json", None
    checks = [(plan.get("schema_version") == "rollback-plan.v1", "schema_version mismatch"), (plan.get("operation") == "rollback_action", "operation mismatch"), (plan.get("run_id") == run_id, "run_id mismatch"), (plan.get("mode") == "dry-run", "mode mismatch"), (plan.get("action_id") == action_id, "action_id mismatch"), (plan.get("preflight", {}).get("status") == "pass", "preflight not pass")]
    for ok, reason in checks:
        if not ok:
            return False, reason, plan
    return True, None, plan


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
    parser = argparse.ArgumentParser(
        prog="safeloop",
        description="SafeLoop: local watchdog, tamper-evident run timeline, and covered local-file rollback.",
    )
    try:
        package_version = version("safeloop")
    except PackageNotFoundError:
        package_version = "0.2.0"
    parser.add_argument("--version", action="version", version=f"%(prog)s {package_version}")
    sub = parser.add_subparsers(dest="cmd", required=True)
    demo = sub.add_parser("demo", help="Run a local SafeLoop demo and write packet artifacts.")
    demo.add_argument("--output-dir", help="Directory for demo repo, runs, and demo-summary.json. Defaults to a temp directory.")
    demo.add_argument("--force", action="store_true", help="Replace existing demo files under --output-dir.")
    demo.add_argument("--json", action="store_true", help="Print machine-readable demo summary.")
    doctor = sub.add_parser("doctor", help="Check local SafeLoop install, repo, and packet-upload readiness.")
    doctor.add_argument("--repo", default=".", help="Repository root to inspect. Defaults to current directory.")
    doctor.add_argument("--json", action="store_true", help="Print machine-readable doctor report.")
    init = sub.add_parser("init", help="Initialize SafeLoop local agent config.")
    init.add_argument("--agent", required=True, choices=["codex"], help="Agent profile to initialize.")
    init.add_argument("--repo", default=".", help="Repository root to initialize. Defaults to current directory.")
    init.add_argument("--force", action="store_true", help="Overwrite existing SafeLoop init files.")
    init.add_argument("--json", action="store_true", help="Print machine-readable init result.")
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
    r.add_argument("--groups", action="store_true")
    ex = sub.add_parser(
        "explain",
        help="Explain rollback, compensation, manual handoff, and action groups for a run.",
        formatter_class=argparse.RawTextHelpFormatter,
        description=(
            "Explain a SafeLoop run in operator language:\n"
            "- rollback covers verified local repo files.\n"
            "- compensation/manual handoff covers actions outside the local repo.\n"
            "- action groups bundle related files, hunks, and checkpoints for review."
        ),
    )
    ex.add_argument("run_dir")
    ex.add_argument("--json", action="store_true")
    pc = sub.add_parser("policy-check")
    pc.add_argument("run_dir")
    pc.add_argument("--policy", required=True)
    pc.add_argument("--json", action="store_true")
    pc.add_argument("--suggest-rollback", action="store_true")
    comp = sub.add_parser("compensate")
    comp.add_argument("run_dir", nargs="?")
    comp.add_argument("extra", nargs="*")
    comp.add_argument("--side-effect", dest="side_effect_id")
    comp.add_argument("--action", dest="action_id")
    comp.add_argument("--dry-run", action="store_true")
    comp.add_argument("--json", action="store_true")
    comp.add_argument("--effect-id")
    comp.add_argument("--status")
    comp.add_argument("--operator", default="unknown")
    comp.add_argument("--evidence")
    comp.add_argument("--quote-or-field")
    comp.add_argument("--notes")
    external = sub.add_parser("external", help="Record external side effects for compensation/manual review.")
    external_sub = external.add_subparsers(dest="external_cmd", required=True)
    external_record = external_sub.add_parser("record", help="Append RUN_DIR/external-effects.jsonl.")
    external_record.add_argument("run_dir")
    external_record.add_argument("--kind", required=True)
    external_record.add_argument("--target", required=True)
    external_record.add_argument("--action", required=True)
    external_record.add_argument("--evidence", required=True)
    external_record.add_argument("--quote-or-field", required=True)
    external_record.add_argument("--capability", default="manual")
    op = sub.add_parser("operator-packet", help="Generate a SafeLoop operator packet from a run directory.")
    op.add_argument("run_dir")
    op.add_argument("--v2", action="store_true", help="Generate Operator Packet v2 (default).")
    op.add_argument("--output", help="Output path; defaults to RUN_DIR/operator-packet-v2.md")
    op.add_argument("--external-evidence", action="append", default=[], help="Path or reference for outside-action evidence. Can be repeated.")
    op.add_argument("--compensation-adapter", default="manual", help="Compensation adapter label for external evidence rows.")
    op.add_argument("--write-manifest", action="store_true", help="Write RUN_DIR/operator-packet-manifest.json alongside the packet.")
    op_verify = sub.add_parser("operator-packet-verify", help="Verify an operator packet manifest against local packet/source artifact hashes.")
    op_verify.add_argument("run_dir")
    op_verify.add_argument("--manifest", help="Manifest path; defaults to RUN_DIR/operator-packet-manifest.json")
    hr = sub.add_parser("html-report")
    hr.add_argument("run_dir")
    hr.add_argument("--output", help="HTML output path; defaults to <run_dir>/safeloop-readiness-report.html")
    dh = sub.add_parser("html-doc")
    dh.add_argument("path")
    dh.add_argument("--output", required=True)
    dp = sub.add_parser("html-docs-packet")
    dp.add_argument("repo_root", nargs="?", default=".")
    dp.add_argument("--output", help="HTML output path; defaults to docs/safeloop-docs-demo-packet.html")
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
    rb_plan.add_argument("--action")
    rb_plan.add_argument("--include-compensation", action="store_true")
    rb_plan.add_argument("--policy")
    rb_apply = rb_sub.add_parser("apply")
    rb_apply.add_argument("run_dir")
    rb_apply.add_argument("run_id")
    rb_apply.add_argument("checkpoint_id", nargs="?")
    rb_apply.add_argument("--to-start", action="store_true")
    rb_apply.add_argument("--from-checkpoint", dest="from_checkpoint")
    rb_apply.add_argument("--files", nargs="+", default=[])
    rb_apply.add_argument("--hunks", nargs="+", default=[])
    rb_apply.add_argument("--action")
    args = parser.parse_args(argv)

    if args.cmd == "demo":
        try:
            summary = _run_demo(output_dir=args.output_dir, force=args.force)
        except (FileExistsError, RuntimeError, subprocess.CalledProcessError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(summary, indent=2, sort_keys=True))
        else:
            print("SafeLoop demo complete")
            print(f"workspace: {summary['workspace']}")
            print(f"run id: {summary['run_id']}")
            print(f"run dir: {summary['run_dir']}")
            print(f"operator packet: {summary['operator_packet']}")
            print(f"operator packet manifest: {summary['operator_packet_manifest']}")
            print("external exact rollback: false")
        return 0
    if args.cmd == "doctor":
        report = _doctor_report(Path(args.repo))
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            _print_doctor(report)
        return 1 if report["status"] == "errors" else 0
    if args.cmd == "init":
        try:
            result = _init_agent(Path(args.repo), agent=args.agent, force=args.force)
        except (ValueError, FileExistsError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print(f"SafeLoop initialized for {result['agent']}")
            for path in result["written"]:
                print(f"written: {path}")
            for path in result["unchanged"]:
                print(f"unchanged: {path}")
            print(f"packet dir: {result['packet_dir']}")
        return 0
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
        if args.groups:
            artifact = build_rollback_groups(Path(args.run_dir))
            if args.json:
                print(json.dumps(artifact, indent=2, sort_keys=True))
            else:
                print_rollback_groups(artifact)
            return 0
        summary = _review_summary(Path(args.run_dir))
        if args.json:
            print(json.dumps(summary, indent=2, sort_keys=True))
        else:
            _print_review(summary)
        return 0
    if args.cmd == "explain":
        artifact = build_rollback_groups(Path(args.run_dir))
        if args.json:
            print(json.dumps(artifact, indent=2, sort_keys=True))
        else:
            print_rollback_groups(artifact)
        return 0
    if args.cmd == "policy-check":
        try:
            result = run_policy_check(Path(args.run_dir), Path(args.policy))
        except PolicyCheckError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        if args.suggest_rollback:
            suggestion = build_policy_rollback_suggestion(Path(args.run_dir), Path(args.policy), result)
            if args.json:
                print(json.dumps(suggestion, indent=2, sort_keys=True))
            else:
                print("SafeLoop policy rollback suggestion")
                print(f"status: {suggestion['status']}")
                print(f"exact rollback: {str(suggestion['exact_rollback']).lower()}")
                print(f"manual review required: {str(suggestion['manual_review_required']).lower()}")
                print(f"policy-rollback-suggestion.json: {Path(args.run_dir) / 'policy-rollback-suggestion.json'}")
            return 1 if result["violations"] else 0
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print("SafeLoop do-not-do policy check")
            print(f"status: {result['status']}")
            print(f"violations: {result['violation_count']}")
            print(f"policy-check-result.json: {Path(args.run_dir) / 'policy-check-result.json'}")
        return 1 if result["violations"] else 0
    if args.cmd == "compensate":
        if args.run_dir == "result":
            if not args.extra:
                print("compensate result requires RUN_DIR", file=sys.stderr)
                return 2
            try:
                artifact = create_compensation_result(
                    Path(args.extra[0]),
                    effect_id=args.effect_id or "",
                    status=args.status or "",
                    operator=args.operator,
                    evidence_path_or_url=args.evidence or "",
                    quote_or_field=args.quote_or_field or "",
                    notes=args.notes,
                )
            except CompensationResultValidationError as exc:
                print(str(exc), file=sys.stderr)
                return 2
            if args.json:
                print(json.dumps(artifact, indent=2, sort_keys=True))
            else:
                print("SafeLoop compensation result")
                print(f"status: {artifact['status']}")
                print(f"effect id: {artifact['effect_id']}")
                print(f"exact rollback: {str(artifact['exact_rollback']).lower()}")
                print(f"compensation-result.json: {Path(args.extra[0]) / 'compensation-result.json'}")
            return 0
        if not args.run_dir:
            print("compensate requires RUN_DIR", file=sys.stderr)
            return 2
        artifact = build_compensation_plan(Path(args.run_dir), side_effect_id=args.side_effect_id, action_id=args.action_id, dry_run=args.dry_run)
        if args.json:
            print(json.dumps(artifact, indent=2, sort_keys=True))
        else:
            print("SafeLoop compensation plan")
            print(f"status: {artifact['status']}")
            print(f"exact rollback: {str(artifact['exact_rollback']).lower()}")
            print(f"compensation-plan.json: {Path(args.run_dir) / 'compensation-plan.json'}")
        return 0
    if args.cmd == "external":
        if args.external_cmd == "record":
            try:
                effect = record_external_effect(
                    Path(args.run_dir),
                    kind=args.kind,
                    target=args.target,
                    action=args.action,
                    evidence_path_or_url=args.evidence,
                    quote_or_field=args.quote_or_field,
                    compensation_capability=args.capability,
                )
            except ExternalEffectValidationError as exc:
                print(str(exc), file=sys.stderr)
                return 2
            print(f"External side effect recorded: {effect['effect_id']}")
            print("exact rollback: false")
            print(f"status: {effect['status']}")
            return 0
    if args.cmd == "operator-packet":
        run_dir = Path(args.run_dir)
        if not (run_dir / "run.json").exists():
            print(f"missing run.json in run directory: {run_dir}", file=sys.stderr)
            return 2
        output = Path(args.output) if args.output else run_dir / "operator-packet-v2.md"
        if args.write_manifest:
            try:
                validate_operator_packet_manifest_packet_path(run_dir, output)
            except ValueError as exc:
                print(str(exc), file=sys.stderr)
                return 2
        out = write_operator_packet_v2(
            run_dir,
            output_path=output,
            external_evidence=args.external_evidence,
            compensation_adapter=args.compensation_adapter,
        )
        packet = out.read_text(encoding="utf-8")
        marker = "## 7. Recommended next action\n"
        next_action = "unknown"
        if marker in packet:
            next_action = packet.split(marker, 1)[1].splitlines()[0].strip() or "unknown"
        print(f"Operator packet v2 written: {out}")
        if args.write_manifest:
            manifest = write_operator_packet_manifest(run_dir, out)
            print(f"Operator packet manifest written: {run_dir / 'operator-packet-manifest.json'}")
            print(f"manifest status: {manifest['verification']['status']}")
        print(f"recommended next action: {next_action}")
        return 0
    if args.cmd == "operator-packet-verify":
        run_dir = Path(args.run_dir)
        try:
            result = verify_operator_packet_manifest(
                run_dir,
                manifest_path=Path(args.manifest) if args.manifest else None,
            )
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            print(f"operator-packet verification: invalid", file=sys.stderr)
            print(str(exc), file=sys.stderr)
            return 2
        verification = result["verification"]
        print(f"operator-packet verification: {verification['status']}")
        for issue in verification.get("issues", []):
            print(f"issue: {issue}")
        return 0 if verification["status"] == "valid" else 1
    if args.cmd == "html-report":
        output = write_readiness_html(Path(args.run_dir), Path(args.output) if args.output else None)
        print(f"SafeLoop HTML readiness report: {output}")
        return 0
    if args.cmd == "html-doc":
        output = write_markdown_doc_html(Path(args.path), Path(args.output))
        print(f"SafeLoop HTML doc report: {output}")
        return 0
    if args.cmd == "html-docs-packet":
        output = write_docs_packet_html(Path(args.repo_root), Path(args.output) if args.output else None)
        print(f"SafeLoop HTML docs/demo packet: {output}")
        return 0
    if args.cmd == "rollback":
        run_dir = Path(args.run_dir)
        apply_mode = args.rollback_cmd == "apply"
        if getattr(args, "rollback_cmd", None) == "plan" and getattr(args, "policy", None) and not args.files and not args.hunks and not args.action:
            try:
                result = run_policy_check(run_dir, Path(args.policy))
            except PolicyCheckError as exc:
                print(str(exc), file=sys.stderr)
                return 2
            suggestion = build_policy_rollback_suggestion(run_dir, Path(args.policy), result)
            print("Policy rollback suggestion: " + ("PASS" if suggestion["status"] == "pass" else "REVIEW"))
            print(f"policy-rollback-suggestion.json: {run_dir / 'policy-rollback-suggestion.json'}")
            return 1 if result["violations"] else 0
        if args.action:
            if apply_mode:
                ok, reason, plan = _require_matching_action_plan(run_dir, args.run_id, args.action)
                if not ok:
                    artifact = {"schema_version": "rollback-result.v1", "operation": "rollback_action", "run_id": args.run_id, "mode": "apply", "action_id": args.action, "status": "blocked", "preflight": {"status": "blocked"}, "blocked_reason": reason or "plan mismatch"}
                    atomic_json(run_dir / "rollback-result.json", artifact)
                    digests = {"rollback-result.json": sha_file(run_dir / "rollback-result.json")}
                    if plan is not None and (run_dir / "rollback-plan.json").exists():
                        digests["rollback-plan.json"] = sha_file(run_dir / "rollback-plan.json")
                    _append_rollback_event(run_dir, "rollback_blocked", {"operation": "rollback_action", "action_id": args.action, "reason": artifact["blocked_reason"], "artifact_digests": digests})
                    print("Rollback apply blocked")
                    print(f"blocked reason: {artifact['blocked_reason']}")
                    print(f"rollback-result.json: {run_dir / 'rollback-result.json'}")
                    return 1
            artifact = _action_rollback(run_dir, args.run_id, args.action, apply=apply_mode)
            if not apply_mode:
                artifact["compensation"] = compensation_section_for_rollback(run_dir, action_id=args.action, include_compensation=args.include_compensation)
                atomic_json(run_dir / "rollback-plan.json", artifact)
            if apply_mode:
                event_type = "rollback_applied" if artifact["status"] == "applied" else "rollback_blocked"
                _append_rollback_event(run_dir, event_type, {"operation": "rollback_action", "action_id": args.action, "rollback_status": artifact["status"], "artifact_digests": {"rollback-plan.json": sha_file(run_dir / "rollback-plan.json"), "rollback-result.json": sha_file(run_dir / "rollback-result.json")}})
                print("Rollback apply complete" if artifact["status"] == "applied" else "Rollback apply blocked")
                print(f"rollback-result.json: {run_dir / 'rollback-result.json'}")
            else:
                _append_rollback_event(run_dir, "rollback_plan_created", {"operation": "rollback_action", "action_id": args.action, "artifact_digests": {"rollback-plan.json": sha_file(run_dir / "rollback-plan.json")}})
                print("Rollback plan: PASS" if artifact["preflight"]["status"] == "pass" else "Rollback plan: BLOCKED")
                print(f"rollback-plan.json: {run_dir / 'rollback-plan.json'}")
            print("SafeLoop rollback operator summary")
            print(f"run id: {artifact['run_id']}")
            print(f"action id: {artifact['action_id']}")
            print(f"mode: {artifact['mode']}")
            print(f"file counts: {artifact['file_counts']}")
            print(f"external side effects: {artifact['external_side_effects']['classification']}")
            if artifact.get("blocked_reason"):
                print(f"blocked reason: {artifact['blocked_reason']}")
            return 1 if artifact["preflight"]["status"] == "blocked" and apply_mode else 0
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
                artifact["compensation"] = compensation_section_for_rollback(run_dir, action_id=args.action, include_compensation=args.include_compensation)
                atomic_json(run_dir / "rollback-plan.json", artifact)
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
                artifact["compensation"] = compensation_section_for_rollback(run_dir, action_id=args.action, include_compensation=args.include_compensation)
                atomic_json(run_dir / "rollback-plan.json", artifact)
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
                artifact["compensation"] = compensation_section_for_rollback(run_dir, action_id=args.action, include_compensation=args.include_compensation)
                atomic_json(run_dir / "rollback-plan.json", artifact)
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
            artifact["compensation"] = compensation_section_for_rollback(run_dir, action_id=args.action, include_compensation=args.include_compensation)
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
