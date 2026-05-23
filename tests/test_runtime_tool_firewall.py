from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from safeloop.external_outbox import read_external_outbox
from safeloop.operator_packet import render_operator_packet_v2
from safeloop.operator_packet_manifest import write_operator_packet_manifest
from safeloop.quarantine import list_quarantine
from safeloop.runtime_tool_exec import execute_tool_request, read_runtime_tool_exec_events
from safeloop.runtime_tool_firewall import RuntimeToolFirewallError, firewall_preflight, read_runtime_tool_firewall_events, route_tool_action

ROOT = Path(__file__).resolve().parents[1]


def make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return workspace


def make_run_dir(tmp_path: Path) -> Path:
    run_dir = tmp_path / "run"
    (run_dir / "verification").mkdir(parents=True)
    (run_dir / "run.json").write_text(
        json.dumps(
            {
                "run_id": "run-firewall",
                "task_id": "runtime-tool-firewall-v4",
                "status": "completed",
                "started_at": "2026-05-22T00:00:00+00:00",
                "ended_at": "2026-05-22T00:00:01+00:00",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (run_dir / "rollback-plan.json").write_text(
        json.dumps(
            {
                "schema_version": "rollback-plan.v1",
                "run_id": "run-firewall",
                "status": "ok",
                "checkpoint_id": "cp-0001",
                "covered_local_file_changes": {"modified": ["service.md"], "created": [], "deleted": []},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (run_dir / "verification" / "verify-artifacts-result.json").write_text(
        json.dumps({"schema_version": "verify-artifacts-result.v1", "status": "valid", "issues": []}, indent=2),
        encoding="utf-8",
    )
    return run_dir


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    return subprocess.run(
        [sys.executable, "-m", "safeloop.cli", *args],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )


def test_destructive_local_file_routes_to_quarantine(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    run_dir = make_run_dir(tmp_path)
    target = workspace / "scratch.txt"
    target.write_text("temporary\n", encoding="utf-8")

    event = route_tool_action(
        run_dir,
        tool="rm",
        action="delete",
        target="scratch.txt",
        workspace_root=workspace,
        reason="cleanup generated scratch file",
        actor="codex",
    )

    assert event["route"] == "quarantine"
    assert event["exact_rollback"] is True
    assert event["manual_review_required"] is False
    assert event["quarantine_item_id"].startswith("q-")
    assert not target.exists()
    assert list_quarantine(run_dir)["items"][0]["item_id"] == event["quarantine_item_id"]
    persisted = read_runtime_tool_firewall_events(run_dir)[0]
    assert persisted["event_id"] == "fw-0001"
    assert persisted["prev_event_hash"] is None
    assert persisted["event_hash"].startswith("sha256:")
    assert persisted["dry_run"] is False


def test_destructive_local_directory_routes_to_recursive_quarantine(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    run_dir = make_run_dir(tmp_path)
    bundle = workspace / "bundle"
    bundle.mkdir()
    (bundle / "README.txt").write_text("bundle\n", encoding="utf-8")

    event = route_tool_action(
        run_dir,
        tool="rm",
        action="delete",
        target="bundle",
        workspace_root=workspace,
        target_kind="local_directory",
        reason="cleanup generated bundle",
    )

    item = list_quarantine(run_dir)["items"][0]
    assert event["route"] == "quarantine"
    assert event["quarantine_item_id"] == item["item_id"]
    assert item["restore_type"] == "directory"
    assert not bundle.exists()


def test_external_write_routes_to_outbox_without_dispatch(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)

    event = route_tool_action(
        run_dir,
        tool="curl",
        action="post",
        target="https://example.test/hooks/review",
        reason="send review webhook after operator approval",
        actor="codex",
    )

    assert event["route"] == "external_outbox"
    assert event["outbox_id"] == "outbox-0001"
    assert event["exact_rollback"] is False
    assert event["external_dispatch_allowed"] is False
    outbox = read_external_outbox(run_dir)
    assert outbox["counts"]["pending"] == 1
    assert outbox["items"][0]["kind"] == "webhook"
    assert outbox["items"][0]["dispatch_allowed"] is False
    assert not (run_dir / "external-effects.jsonl").exists()


def test_unknown_tool_routes_to_manual_review_without_side_effect(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)

    event = route_tool_action(
        run_dir,
        tool="mystery",
        action="transmogrify",
        target="opaque-ref",
        reason="agent requested an unknown capability",
    )

    assert event["route"] == "manual_review"
    assert event["manual_review_required"] is True
    assert event["route_reason"] == "unrecognized tool semantics"
    assert not (run_dir / "external-outbox.json").exists()
    assert list_quarantine(run_dir)["items"] == []


def test_firewall_log_is_hash_chained_and_locked_for_parallel_manual_review(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)

    def route(index: int) -> dict:
        return route_tool_action(
            run_dir,
            tool="mystery",
            action=f"transmogrify-{index}",
            target=f"opaque-ref-{index}",
            reason="agent requested an unknown capability",
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(route, range(8)))

    events = read_runtime_tool_firewall_events(run_dir)

    assert [event["event_id"] for event in events] == [f"fw-{index:04d}" for index in range(1, 9)]
    assert events[0]["prev_event_hash"] is None
    for previous, current in zip(events, events[1:]):
        assert current["prev_event_hash"] == previous["event_hash"]
        assert current["event_hash"].startswith("sha256:")


def test_firewall_log_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)
    route_tool_action(
        run_dir,
        tool="mystery",
        action="transmogrify",
        target="opaque-ref",
        reason="agent requested an unknown capability",
    )
    path = run_dir / "runtime-tool-firewall.jsonl"
    event = json.loads(path.read_text(encoding="utf-8"))
    event["route"] = "allow_read_only"
    path.write_text(json.dumps(event, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(RuntimeToolFirewallError, match="event_hash mismatch"):
        read_runtime_tool_firewall_events(run_dir)


def test_read_only_tool_routes_to_allow_read_only(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)

    event = route_tool_action(
        run_dir,
        tool="rg",
        action="search",
        target="README.md",
        reason="inspect docs",
    )

    assert event["route"] == "allow_read_only"
    assert event["manual_review_required"] is False
    assert event["external_dispatch_allowed"] is False
    assert not (run_dir / "external-outbox.json").exists()


def test_firewall_preflight_uses_safeloop_run_env_and_action_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run_dir = make_run_dir(tmp_path)
    monkeypatch.setenv("SAFELOOP_RUN_DIR", str(run_dir))
    monkeypatch.setenv("SAFELOOP_RUN_ID", "run-firewall")
    monkeypatch.setenv("SAFELOOP_ACTION_ID", "act-preflight")

    event = firewall_preflight(tool="rg", action="search", target="README.md", reason="inspect docs")

    assert event["source"] == "runtime_helper"
    assert event["action_id"] == "act-preflight"
    assert event["route"] == "allow_read_only"
    persisted = read_runtime_tool_firewall_events(run_dir)[0]
    assert persisted["action_id"] == "act-preflight"
    assert persisted["source"] == "runtime_helper"


def test_firewall_preflight_strict_manual_review_records_then_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run_dir = make_run_dir(tmp_path)
    monkeypatch.setenv("SAFELOOP_RUN_DIR", str(run_dir))
    monkeypatch.setenv("SAFELOOP_RUN_ID", "run-firewall")

    with pytest.raises(RuntimeToolFirewallError, match="requires manual review"):
        firewall_preflight(
            tool="mystery",
            action="transmogrify",
            target="opaque-ref",
            reason="agent requested an unknown capability",
            strict=True,
        )

    events = read_runtime_tool_firewall_events(run_dir)
    assert len(events) == 1
    assert events[0]["route"] == "manual_review"
    assert events[0]["manual_review_required"] is True


def test_firewall_preflight_validates_env_run_id_before_writing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run_dir = make_run_dir(tmp_path)
    monkeypatch.setenv("SAFELOOP_RUN_DIR", str(run_dir))
    monkeypatch.setenv("SAFELOOP_RUN_ID", "wrong-run")

    with pytest.raises(RuntimeToolFirewallError, match="SAFELOOP_RUN_ID mismatch"):
        firewall_preflight(tool="rg", action="search", target="README.md", reason="inspect docs")

    assert not (run_dir / "runtime-tool-firewall.jsonl").exists()


def test_dry_run_classifies_without_writing_firewall_or_downstream_artifacts(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    run_dir = make_run_dir(tmp_path)
    target = workspace / "scratch.txt"
    target.write_text("temporary\n", encoding="utf-8")

    event = route_tool_action(
        run_dir,
        tool="rm",
        action="delete",
        target="scratch.txt",
        workspace_root=workspace,
        reason="cleanup generated scratch file",
        dry_run=True,
    )

    assert event["route"] == "quarantine"
    assert event["dry_run"] is True
    assert event["exact_rollback"] is False
    assert event["would_create_artifacts"] == ["quarantine"]
    assert target.exists()
    assert not (run_dir / "runtime-tool-firewall.jsonl").exists()
    assert list_quarantine(run_dir)["items"] == []


def test_firewall_exec_runs_allowlisted_read_only_command_and_records_output(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    run_dir = make_run_dir(tmp_path)
    (workspace / "note.txt").write_text("safe read\n", encoding="utf-8")

    result = execute_tool_request(
        run_dir,
        tool="cat",
        action="read",
        target="note.txt",
        command=["cat", "note.txt"],
        workspace_root=workspace,
        reason="inspect note",
        actor="codex",
    )

    assert result["status"] == "executed"
    assert result["executed"] is True
    assert result["exit_code"] == 0
    firewall = read_runtime_tool_firewall_events(run_dir)[0]
    assert firewall["route"] == "allow_read_only"
    assert firewall["source"] == "exec_wrapper"
    exec_event = read_runtime_tool_exec_events(run_dir)[0]
    assert exec_event["status"] == "executed"
    assert exec_event["firewall_event_id"] == firewall["event_id"]
    assert exec_event["command"] == ["cat", "note.txt"]
    assert (run_dir / exec_event["stdout_path"]).read_text(encoding="utf-8") == "safe read\n"
    assert (run_dir / exec_event["stderr_path"]).read_text(encoding="utf-8") == ""


def test_firewall_exec_routes_destructive_command_to_quarantine_without_running_shell_command(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    run_dir = make_run_dir(tmp_path)
    target = workspace / "scratch.txt"
    target.write_text("temporary\n", encoding="utf-8")

    result = execute_tool_request(
        run_dir,
        tool="rm",
        action="delete",
        target="scratch.txt",
        command=["rm", "scratch.txt"],
        workspace_root=workspace,
        reason="cleanup generated scratch file",
    )

    assert result["status"] == "blocked"
    assert result["executed"] is False
    assert not target.exists()
    firewall = read_runtime_tool_firewall_events(run_dir)[0]
    assert firewall["route"] == "quarantine"
    exec_event = read_runtime_tool_exec_events(run_dir)[0]
    assert exec_event["block_reason"] == "firewall route quarantine does not permit execution"
    assert list_quarantine(run_dir)["items"][0]["item_id"] == firewall["quarantine_item_id"]


def test_firewall_exec_routes_external_command_to_outbox_without_dispatch(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)

    result = execute_tool_request(
        run_dir,
        tool="curl",
        action="post",
        target="https://example.test/hooks/review",
        command=["curl", "https://example.test/hooks/review"],
        reason="send review webhook after operator approval",
    )

    assert result["status"] == "blocked"
    assert result["executed"] is False
    assert read_runtime_tool_firewall_events(run_dir)[0]["route"] == "external_outbox"
    assert read_runtime_tool_exec_events(run_dir)[0]["block_reason"] == "firewall route external_outbox does not permit execution"
    assert read_external_outbox(run_dir)["counts"]["pending"] == 1


def test_firewall_exec_blocks_command_mismatch_even_when_route_is_read_only(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    run_dir = make_run_dir(tmp_path)
    (workspace / "note.txt").write_text("safe read\n", encoding="utf-8")
    marker = workspace / "marker.txt"

    result = execute_tool_request(
        run_dir,
        tool="cat",
        action="read",
        target="note.txt",
        command=[sys.executable, "-c", "from pathlib import Path; Path('marker.txt').write_text('ran')"],
        workspace_root=workspace,
        reason="inspect note",
    )

    assert result["status"] == "blocked"
    assert result["executed"] is False
    assert not marker.exists()
    assert read_runtime_tool_firewall_events(run_dir)[0]["route"] == "allow_read_only"
    assert "does not match tool" in read_runtime_tool_exec_events(run_dir)[0]["block_reason"]


def test_firewall_cli_route_outputs_json(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)

    result = run_cli(
        "firewall",
        "route",
        str(run_dir),
        "--tool",
        "github",
        "--action",
        "comment",
        "--target",
        "https://github.com/clawdia-saka/safeloop/issues/1",
        "--reason",
        "post operator note",
        "--json",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    event = json.loads(result.stdout)
    assert event["route"] == "external_outbox"
    assert event["outbox_id"] == "outbox-0001"
    assert event["source"] == "cli"


def test_firewall_cli_exec_outputs_json_and_records_exec_artifact(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    run_dir = make_run_dir(tmp_path)
    (workspace / "note.txt").write_text("safe read\n", encoding="utf-8")

    result = run_cli(
        "firewall",
        "exec",
        str(run_dir),
        "--tool",
        "cat",
        "--action",
        "read",
        "--target",
        "note.txt",
        "--workspace-root",
        str(workspace),
        "--reason",
        "inspect note",
        "--json",
        "--",
        "cat",
        "note.txt",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "executed"
    exec_event = payload["exec_event"]
    assert exec_event["status"] == "executed"
    assert (run_dir / exec_event["stdout_path"]).read_text(encoding="utf-8") == "safe read\n"


def test_firewall_cli_dry_run_strict_manual_review_returns_nonzero_without_writing(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)

    result = run_cli(
        "firewall",
        "route",
        str(run_dir),
        "--tool",
        "mystery",
        "--action",
        "transmogrify",
        "--target",
        "opaque-ref",
        "--reason",
        "agent requested an unknown capability",
        "--dry-run",
        "--strict",
        "--json",
    )

    assert result.returncode == 1, result.stdout + result.stderr
    event = json.loads(result.stdout)
    assert event["route"] == "manual_review"
    assert event["dry_run"] is True
    assert not (run_dir / "runtime-tool-firewall.jsonl").exists()
    assert not (run_dir / "external-outbox.json").exists()


def test_operator_packet_and_manifest_surface_firewall_artifact(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)
    route_tool_action(
        run_dir,
        tool="mystery",
        action="transmogrify",
        target="opaque-ref",
        reason="agent requested an unknown capability",
    )

    packet = render_operator_packet_v2(run_dir)
    manifest = write_operator_packet_manifest(run_dir)

    assert "runtime-tool-firewall.jsonl: present" in packet
    assert "| fw-0001 | runtime_tool_firewall | opaque-ref | manual_review | false | runtime-tool-firewall.jsonl |" in packet
    assert "recommended next action: runtime_tool_firewall_manual_review_required" in packet
    firewall = next(item for item in manifest["source_artifacts"] if item["path"] == "runtime-tool-firewall.jsonl")
    assert firewall["present"] is True
    assert firewall["required"] is False


def test_operator_packet_surfaces_action_event_evidence_for_correlated_firewall_event(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path)
    route_tool_action(
        run_dir,
        tool="mystery",
        action="transmogrify",
        target="opaque-ref",
        reason="agent requested an unknown capability",
        source="runtime_helper",
        action_id="act-fw",
    )

    packet = render_operator_packet_v2(run_dir)

    assert "| fw-0001 | runtime_tool_firewall | opaque-ref | manual_review | false | runtime-tool-firewall.jsonl; action-events.jsonl |" in packet


def test_operator_packet_and_manifest_surface_runtime_exec_artifact(tmp_path: Path) -> None:
    workspace = make_workspace(tmp_path)
    run_dir = make_run_dir(tmp_path)
    (workspace / "note.txt").write_text("safe read\n", encoding="utf-8")
    execute_tool_request(
        run_dir,
        tool="cat",
        action="read",
        target="note.txt",
        command=["cat", "note.txt"],
        workspace_root=workspace,
        reason="inspect note",
    )

    packet = render_operator_packet_v2(run_dir)
    manifest = write_operator_packet_manifest(run_dir)

    assert "runtime-tool-exec.jsonl: present" in packet
    assert "| runtime_tool_exec | note.txt | executed | false | runtime-tool-exec.jsonl; runtime-tool-firewall.jsonl;" in packet
    exec_artifact = next(item for item in manifest["source_artifacts"] if item["path"] == "runtime-tool-exec.jsonl")
    assert exec_artifact["present"] is True
    assert exec_artifact["required"] is False
    stdout_artifact = next(item for item in manifest["source_artifacts"] if item["path"].endswith("/stdout.txt"))
    assert stdout_artifact["present"] is True
    assert stdout_artifact["required"] is True


def test_runtime_tool_firewall_docs_pin_default_routes() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    spec = (ROOT / "docs" / "specs" / "runtime-tool-firewall-v1.md").read_text(encoding="utf-8")
    quarantine = (ROOT / "docs" / "quarantine.md").read_text(encoding="utf-8")

    for text in [readme, spec, quarantine]:
        assert "runtime-tool-firewall.jsonl" in text
        assert "quarantine" in text
        assert "external-outbox.json" in text
        assert "manual review" in text
    assert "firewall_preflight" in readme
    assert "firewall_preflight" in spec
    assert "firewall exec" in readme
    assert "runtime-tool-exec.jsonl" in spec
