from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from safeloop.agent_watchdog import atomic_json, verify_run, watch_run
from safeloop.control_plane.lifecycle import ApprovalValidationError
from safeloop.control_plane.sqlite_lifecycle import SQLiteApprovalLifecycleStore
from safeloop.local_anchor import canonical_sha256, create_local_anchor, verify_local_anchor

REQUEST_SCHEMA = "safeloop.execute-request.v1"
POLICY_SCHEMA = "safeloop.execution-policy.v1"
RECEIPT_SCHEMA = "safeloop.execution-receipt.v1"
SUPPORTED_CLASSES = {"read_only", "repo_write", "shell_write"}
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_KEYS = {"schema_version", "task_id", "run_id", "repo_root", "run_root", "mutation_class", "argv", "action_digest", "policy_version", "policy_ref", "requested_by", "approval_id", "expected_artifact_scope", "timeout_seconds"}


class ExecutionRequestError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ExecutionConfig:
    approval_db: Path
    signing_key: bytes
    policy_root: Path


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sign_policy(policy: dict[str, Any], key: bytes) -> dict[str, Any]:
    unsigned = {k: v for k, v in policy.items() if k != "signature"}
    digest = hmac.new(key, _canonical_json(unsigned), hashlib.sha256).hexdigest()
    return {**unsigned, "signature": f"sha256={digest}"}


def canonical_action_digest(request: dict[str, Any]) -> str:
    return canonical_sha256({k: v for k, v in request.items() if k not in {"action_digest", "approval_id"}})


def load_request(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExecutionRequestError("invalid_request", "request is not valid readable JSON") from exc
    if not isinstance(value, dict):
        raise ExecutionRequestError("invalid_request", "request must be an object")
    return value


def _path(value: Any, name: str, *, must_exist: bool) -> Path:
    if not isinstance(value, str) or not value or "\0" in value:
        raise ExecutionRequestError("unsafe_path", f"{name} must be an absolute path")
    raw = Path(value)
    if not raw.is_absolute() or raw.is_symlink():
        raise ExecutionRequestError("unsafe_path", f"{name} must be an absolute non-symlink path")
    path = raw.resolve(strict=False)
    if any(parent.exists() and parent.is_symlink() for parent in (path, *path.parents)):
        raise ExecutionRequestError("unsafe_path", f"{name} has a symlink ancestor")
    if must_exist and not path.is_dir():
        raise ExecutionRequestError("unsafe_path", f"{name} must be an existing directory")
    return path


def validate_request(raw: dict[str, Any]) -> tuple[dict[str, Any], Path, Path]:
    if set(raw) != _KEYS or raw.get("schema_version") != REQUEST_SCHEMA:
        raise ExecutionRequestError("invalid_request", "request does not match the strict schema")
    for key in ("task_id", "run_id", "policy_version", "policy_ref", "requested_by"):
        if not isinstance(raw[key], str) or not raw[key] or len(raw[key]) > 512:
            raise ExecutionRequestError("invalid_request", f"{key} must be a bounded non-empty string")
    if re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", raw["run_id"]) is None:
        raise ExecutionRequestError("invalid_request", "run_id must match SafeLoop run id semantics")
    if raw["mutation_class"] not in SUPPORTED_CLASSES:
        raise ExecutionRequestError("unsupported_mutation_class", "mutation class is not supported")
    argv = raw["argv"]
    if not isinstance(argv, list) or not argv or len(argv) > 128 or not all(isinstance(v, str) and v and "\0" not in v and len(v) <= 8192 for v in argv):
        raise ExecutionRequestError("invalid_argv", "argv must be a bounded exact string array")
    if not isinstance(raw["action_digest"], str) or not _DIGEST.fullmatch(raw["action_digest"]):
        raise ExecutionRequestError("invalid_request", "action_digest is invalid")
    if raw["approval_id"] is not None and (not isinstance(raw["approval_id"], str) or not raw["approval_id"]):
        raise ExecutionRequestError("invalid_request", "approval_id must be null or a non-empty string")
    scope = raw["expected_artifact_scope"]
    if not isinstance(scope, list) or not scope or not all(isinstance(v, str) and v and not v.startswith("/") and ".." not in Path(v).parts for v in scope):
        raise ExecutionRequestError("invalid_request", "expected_artifact_scope contains an unsafe path")
    timeout = raw["timeout_seconds"]
    if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= 86400:
        raise ExecutionRequestError("invalid_request", "timeout_seconds is out of range")
    repo = _path(raw["repo_root"], "repo_root", must_exist=True)
    run_root = _path(raw["run_root"], "run_root", must_exist=False)
    if run_root == repo or repo in run_root.parents:
        raise ExecutionRequestError("unsafe_path", "run_root must not be inside repo_root")
    if canonical_action_digest(raw) != raw["action_digest"]:
        raise ExecutionRequestError("digest_mismatch", "action_digest does not match request")
    return raw, repo, run_root


def _policy(request: dict[str, Any], config: ExecutionConfig) -> tuple[dict[str, bool], Path, str]:
    root = _path(str(config.policy_root), "policy_root", must_exist=True)
    path = _path(request["policy_ref"], "policy_ref", must_exist=False)
    if root not in path.parents or not path.is_file():
        raise ExecutionRequestError("policy_denied", "policy_ref must be an existing file")
    try:
        policy = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExecutionRequestError("policy_denied", "policy is not valid readable JSON") from exc
    if not isinstance(policy, dict) or set(policy) != {"schema_version", "policy_version", "policy_id", "mutation_classes", "signature"} or policy["schema_version"] != POLICY_SCHEMA or policy["policy_version"] != request["policy_version"]:
        raise ExecutionRequestError("policy_denied", "policy schema or version binding is invalid")
    signature = policy.get("signature")
    if not isinstance(signature, str) or not hmac.compare_digest(signature, sign_policy(policy, config.signing_key)["signature"]):
        raise ExecutionRequestError("policy_denied", "policy signature is invalid")
    classes = policy["mutation_classes"]
    rule = classes.get(request["mutation_class"]) if isinstance(classes, dict) else None
    if not isinstance(rule, dict) or set(rule) != {"allow", "require_approval"} or rule.get("allow") is not True or not isinstance(rule.get("require_approval"), bool):
        raise ExecutionRequestError("policy_denied", "trusted policy does not allow this class")
    if request["mutation_class"] != "read_only" and rule["require_approval"] is not True:
        raise ExecutionRequestError("policy_denied", "write classes require approval")
    return rule, path, hashlib.sha256(path.read_bytes()).hexdigest()


def execute_request(raw: dict[str, Any], config: ExecutionConfig, *, before_spawn: Callable[[], None] | None = None) -> tuple[int, dict[str, Any]]:
    request, repo, run_root = validate_request(raw)
    rule, policy_path, policy_digest = _policy(request, config)
    store = SQLiteApprovalLifecycleStore(config.approval_db, config.signing_key, ttl=timedelta(minutes=10))
    approval = None
    action = f"execute:{request['mutation_class']}"
    approver = None
    if rule["require_approval"]:
        approval_id = request["approval_id"]
        if not approval_id:
            raise ExecutionRequestError("approval_required", "approval_id is required")
        presented = store.get(approval_id)
        if presented is None:
            raise ExecutionRequestError("approval_invalid", "approval is missing or unknown")
        actors = [e.actor for e in store.list_events(approval_id) if e.event_type == "APPROVED"]
        if len(actors) != 1 or actors[0] == request["requested_by"]:
            raise ExecutionRequestError("self_approval", "approval requires one distinct approving actor")
        approver = actors[0]
        try:
            approval = store.reserve_for_execution(presented, requested_by=request["requested_by"], action=action, subject=request["action_digest"], now=datetime.now(timezone.utc))
        except ApprovalValidationError as exc:
            raise ExecutionRequestError("approval_invalid", str(exc)) from exc
    elif request["approval_id"] is not None:
        raise ExecutionRequestError("approval_invalid", "unexpected approval_id")
    evidence = {"schema_version": "safeloop.execution-precommit.v1", "request": request, "policy": {"path": str(policy_path), "sha256": f"sha256:{policy_digest}"}, "approval": {"approval_id": request["approval_id"], "approver": approver, "status": approval.status if approval else None}}
    try:
        if before_spawn:
            before_spawn()
        code, run_dir = watch_run(request["task_id"], repo, list(request["argv"]), run_root, timeout_sec=request["timeout_seconds"], pre_spawn=lambda p: atomic_json(p / "execution-precommit.json", evidence), run_id=request["run_id"])
        create_local_anchor(run_dir, {"action": action, "subject": request["action_digest"], "approval_id": request["approval_id"]})
        artifacts = verify_run(run_dir)
        anchor = verify_local_anchor(run_dir)
        missing_scope = [rel for rel in request["expected_artifact_scope"] if not (run_dir / rel).is_file()]
        if missing_scope:
            artifacts = {**artifacts, "status": "invalid", "issues": [*artifacts.get("issues", []), "expected artifact scope missing"]}
        ok = code == 0 and artifacts["status"] in {"valid", "warning"} and anchor["status"] == "valid" and not missing_scope
        if approval is not None:
            if ok:
                try:
                    approval = store.complete_execution(approval, requested_by=request["requested_by"], action=action, subject=request["action_digest"], now=datetime.now(timezone.utc))
                except ApprovalValidationError as exc:
                    store.fail_execution(approval, requested_by=request["requested_by"], action=action, subject=request["action_digest"], now=datetime.now(timezone.utc))
                    raise ExecutionRequestError("approval_completion_failed", str(exc)) from exc
            else:
                approval = store.fail_execution(approval, requested_by=request["requested_by"], action=action, subject=request["action_digest"], now=datetime.now(timezone.utc))
    except Exception:
        if approval is not None and store.get(approval.approval_id) == approval:
            store.fail_execution(approval, requested_by=request["requested_by"], action=action, subject=request["action_digest"], now=datetime.now(timezone.utc))
        raise
    run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    checkpoints = sorted((run_dir / "checkpoints").glob("cp-*")) if (run_dir / "checkpoints").exists() else []
    status = "verified" if ok else ("halted" if code == 124 else "failed")
    receipt = {"schema_version": RECEIPT_SCHEMA, "status": status, "request_run_id": request["run_id"], "run_id": run["run_id"], "run_dir": str(run_dir), "exit_code": code, "verification": {"artifacts": artifacts["status"], "anchor": anchor["status"]}, "artifact_refs": [str(run_dir / rel) for rel in request["expected_artifact_scope"]], "binding": {"action_digest": request["action_digest"], "policy_version": request["policy_version"], "policy_ref": str(policy_path), "approval_id": request["approval_id"], "approval_status": approval.status if approval else None}, "rollback_available": bool(checkpoints)}
    return (0 if ok else (code or 1)), receipt


def error_receipt(exc: Exception) -> dict[str, Any]:
    code = exc.code if isinstance(exc, ExecutionRequestError) else "internal_error"
    message = str(exc) if isinstance(exc, ExecutionRequestError) else "execution request failed"
    return {"schema_version": "safeloop.execution-error.v1", "status": "error", "error": {"code": code, "message": message[:512]}}
