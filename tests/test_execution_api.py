from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
import safeloop.execution_api as execution_api

from safeloop.control_plane.sqlite_lifecycle import SQLiteApprovalLifecycleStore
from safeloop.execution_api import ExecutionConfig, ExecutionRequestError, canonical_action_digest, error_receipt, execute_request, sign_policy

KEY = b"x" * 32


def policy(tmp_path: Path, *, read_approval: bool = False) -> Path:
    root = tmp_path / "policies"; root.mkdir(exist_ok=True)
    path = root / "policy.json"
    path.write_text(json.dumps(sign_policy({"schema_version": "safeloop.execution-policy.v1", "policy_version": "p1", "policy_id": "local", "mutation_classes": {"read_only": {"allow": True, "require_approval": read_approval}, "repo_write": {"allow": True, "require_approval": True}, "shell_write": {"allow": True, "require_approval": True}}}, KEY)))
    return path


def config(tmp_path: Path, db: str = "approvals.sqlite3") -> ExecutionConfig:
    return ExecutionConfig(tmp_path / db, KEY, tmp_path / "policies")


def request(tmp_path: Path, repo: Path, policy_path: Path, *, mutation: str = "read_only", argv: list[str] | None = None) -> dict:
    value = {"schema_version": "safeloop.execute-request.v1", "task_id": "task", "run_id": "qr-1", "repo_root": str(repo.resolve()), "run_root": str((tmp_path / "runs").resolve()), "mutation_class": mutation, "argv": argv or [sys.executable, "-c", "print('ok')"], "action_digest": "sha256:" + "0" * 64, "policy_version": "p1", "policy_ref": str(policy_path.resolve()), "requested_by": "requester", "approval_id": None, "expected_artifact_scope": ["run.json", "timeline.jsonl"], "timeout_seconds": 5}
    value["action_digest"] = canonical_action_digest(value)
    return value


def approve(tmp_path: Path, value: dict, *, approver: str = "reviewer") -> None:
    value["approval_id"] = "ap-1"
    # approval_id is intentionally excluded from the action digest.
    store = SQLiteApprovalLifecycleStore(tmp_path / "approvals.sqlite3", KEY)
    now = datetime.now(timezone.utc)
    store.request(approval_id="ap-1", requested_by=value["requested_by"], action=f"execute:{value['mutation_class']}", subject=value["action_digest"], created_at=now)
    store.approve("ap-1", now=now, approved_by=approver)


def test_read_only_executes_and_returns_verified_receipt(tmp_path: Path) -> None:
    repo = tmp_path / "repo"; repo.mkdir()
    value = request(tmp_path, repo, policy(tmp_path))
    code, receipt = execute_request(value, config(tmp_path))
    assert code == 0
    assert receipt["status"] == "verified"
    assert receipt["request_run_id"] == receipt["run_id"] == "qr-1"
    assert receipt["verification"] == {"artifacts": "valid", "anchor": "valid"}
    assert receipt["exit_code"] == 0
    assert json.dumps(receipt, sort_keys=True, separators=(",", ":")) == json.dumps(receipt, sort_keys=True, separators=(",", ":"))


def test_authorization_happens_before_spawn_and_replay_fails(tmp_path: Path) -> None:
    repo = tmp_path / "repo"; repo.mkdir()
    marker = repo / "spawned"
    value = request(tmp_path, repo, policy(tmp_path), mutation="repo_write", argv=[sys.executable, "-c", f"open({str(marker)!r},'w').write('x')"])
    approve(tmp_path, value)
    code, _ = execute_request(value, config(tmp_path))
    assert code == 0 and marker.exists()
    marker.unlink()
    with pytest.raises(ExecutionRequestError, match="stale|tampered|executable"):
        execute_request(value, config(tmp_path))
    assert not marker.exists()


def test_self_approval_and_digest_mismatch_fail_before_spawn(tmp_path: Path) -> None:
    repo = tmp_path / "repo"; repo.mkdir()
    value = request(tmp_path, repo, policy(tmp_path), mutation="repo_write")
    approve(tmp_path, value, approver="requester")
    with pytest.raises(ExecutionRequestError) as caught:
        execute_request(value, config(tmp_path))
    assert caught.value.code == "self_approval"
    value["argv"] = [sys.executable, "-c", "raise SystemExit(99)"]
    with pytest.raises(ExecutionRequestError) as caught:
        execute_request(value, config(tmp_path))
    assert caught.value.code == "digest_mismatch"


def test_unsupported_path_escape_and_argv_are_fail_closed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"; repo.mkdir()
    value = request(tmp_path, repo, policy(tmp_path))
    value["mutation_class"] = "github"; value["action_digest"] = canonical_action_digest(value)
    with pytest.raises(ExecutionRequestError, match="not supported"):
        execute_request(value, config(tmp_path, "db"))
    value = request(tmp_path, repo, policy(tmp_path)); value["run_root"] = str(repo / "runs"); value["action_digest"] = canonical_action_digest(value)
    with pytest.raises(ExecutionRequestError, match="must not be inside"):
        execute_request(value, config(tmp_path, "db"))
    value = request(tmp_path, repo, policy(tmp_path)); value["argv"] = "echo pwned"; value["action_digest"] = canonical_action_digest(value)
    with pytest.raises(ExecutionRequestError) as caught:
        execute_request(value, config(tmp_path, "db"))
    assert caught.value.code == "invalid_argv"


def test_child_nonzero_and_timeout_return_failed_or_halted_receipts(tmp_path: Path) -> None:
    repo = tmp_path / "repo"; repo.mkdir()
    value = request(tmp_path, repo, policy(tmp_path), argv=[sys.executable, "-c", "raise SystemExit(7)"])
    code, receipt = execute_request(value, config(tmp_path, "db"))
    assert code == 7 and receipt["exit_code"] == 7 and receipt["status"] == "failed"
    value = request(tmp_path, repo, policy(tmp_path), argv=[sys.executable, "-c", "import time; time.sleep(10)"])
    value["run_id"] = "qr-timeout"
    value["timeout_seconds"] = 1
    value["action_digest"] = canonical_action_digest(value)
    code, receipt = execute_request(value, config(tmp_path, "db2"))
    assert code != 0 and receipt["exit_code"] != 0 and receipt["status"] == "halted"


def test_missing_expected_artifact_fails_closed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"; repo.mkdir()
    value = request(tmp_path, repo, policy(tmp_path))
    value["expected_artifact_scope"].append("must-exist.json")
    value["action_digest"] = canonical_action_digest(value)
    code, receipt = execute_request(value, config(tmp_path, "db"))
    assert code != 0
    assert receipt["verification"]["artifacts"] == "invalid"
    assert receipt["status"] == "failed"


def test_structured_error_does_not_leak_unexpected_exception_detail() -> None:
    private_detail = "unexpected-runtime-detail"
    payload = error_receipt(RuntimeError(private_detail))
    encoded = json.dumps(payload)
    assert private_detail not in encoded
    assert payload["error"]["code"] == "internal_error"


@pytest.mark.parametrize(("target", "status"), [("verify_run", "invalid"), ("verify_local_anchor", "invalid")])
def test_artifact_or_anchor_verification_failure_is_nonzero(tmp_path: Path, monkeypatch, target: str, status: str) -> None:
    repo = tmp_path / "repo"; repo.mkdir()
    value = request(tmp_path, repo, policy(tmp_path))
    if target == "verify_run":
        monkeypatch.setattr(execution_api, target, lambda _path: {"status": status, "issues": ["tampered"]})
    else:
        monkeypatch.setattr(execution_api, target, lambda _path: {"status": status, "issues": ["invalid anchor"]})
    code, receipt = execute_request(value, config(tmp_path, "db"))
    assert code != 0
    assert status in receipt["verification"].values()
    assert receipt["status"] == "failed"


@pytest.mark.parametrize("mode", ["nonzero", "timeout", "verify", "precommit"])
def test_reserved_approval_transitions_to_failed_and_cannot_retry(tmp_path: Path, monkeypatch, mode: str) -> None:
    repo = tmp_path / "repo"; repo.mkdir()
    marker = repo / "spawned"
    argv = [sys.executable, "-c", f"open({str(marker)!r},'w').write('x'); raise SystemExit(7)"]
    if mode == "timeout": argv = [sys.executable, "-c", "import time; time.sleep(10)"]
    value = request(tmp_path, repo, policy(tmp_path), mutation="repo_write", argv=argv)
    if mode == "timeout": value["timeout_seconds"] = 1; value["action_digest"] = canonical_action_digest(value)
    approve(tmp_path, value)
    if mode == "verify": monkeypatch.setattr(execution_api, "verify_run", lambda _p: {"status": "invalid", "issues": ["tampered"]})
    if mode == "precommit": monkeypatch.setattr(execution_api, "atomic_json", lambda *_a, **_k: (_ for _ in ()).throw(OSError("disk full")))
    if mode == "precommit":
        with pytest.raises(OSError, match="disk full"): execute_request(value, config(tmp_path))
        assert not marker.exists()
    else:
        execute_request(value, config(tmp_path))
    store = SQLiteApprovalLifecycleStore(tmp_path / "approvals.sqlite3", KEY)
    assert store.get("ap-1").status == "FAILED"
    assert store.list_events("ap-1")[-1].event_type == "FAILED"
    with pytest.raises(ExecutionRequestError, match="stale|tampered|executable"):
        execute_request(value, config(tmp_path))


def test_unsigned_tampered_and_out_of_root_policies_fail_closed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"; repo.mkdir(); signed = policy(tmp_path)
    unsigned = tmp_path / "policies" / "unsigned.json"
    unsigned.write_text(json.dumps({k: v for k, v in json.loads(signed.read_text()).items() if k != "signature"}))
    outside = tmp_path / "outside.json"; outside.write_bytes(signed.read_bytes())
    for candidate in (unsigned, outside):
        with pytest.raises(ExecutionRequestError) as caught: execute_request(request(tmp_path, repo, candidate), config(tmp_path))
        assert caught.value.code == "policy_denied"
    data = json.loads(signed.read_text()); data["policy_id"] = "tampered"; signed.write_text(json.dumps(data))
    with pytest.raises(ExecutionRequestError, match="signature"):
        execute_request(request(tmp_path, repo, signed), config(tmp_path))


def test_operator_sign_policy_cli_refuses_overwrite_without_force(tmp_path: Path) -> None:
    root = tmp_path / "policies"; root.mkdir(); key = tmp_path / "operator.key"; key.write_bytes(KEY)
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"schema_version": "safeloop.execution-policy.v1", "policy_version": "p1", "policy_id": "local", "mutation_classes": {}}))
    cmd = [sys.executable, "-m", "safeloop.cli", "sign-execution-policy", "--input", str(source), "--policy-root", str(root), "--output", "policy.json", "--signing-key-file", str(key)]
    assert subprocess.run(cmd, text=True, capture_output=True).returncode == 0
    assert subprocess.run(cmd, text=True, capture_output=True).returncode != 0
    assert subprocess.run([*cmd, "--force"], text=True, capture_output=True).returncode == 0
