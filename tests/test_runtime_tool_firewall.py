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
from safeloop.runtime_tool_firewall import RuntimeToolFirewallError, read_runtime_tool_firewall_events, route_tool_action

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


def test_runtime_tool_firewall_docs_pin_default_routes() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    spec = (ROOT / "docs" / "specs" / "runtime-tool-firewall-v1.md").read_text(encoding="utf-8")
    quarantine = (ROOT / "docs" / "quarantine.md").read_text(encoding="utf-8")

    for text in [readme, spec, quarantine]:
        assert "runtime-tool-firewall.jsonl" in text
        assert "quarantine" in text
        assert "external-outbox.json" in text
        assert "manual review" in text
