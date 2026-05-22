import json
import os
import subprocess
import sys
from pathlib import Path

from safeloop import action_span
from safeloop.action_span import verify_action_events
from safeloop.agent_watchdog import verify_run, watch_run
from safeloop.runtime_tool_firewall import read_runtime_tool_firewall_events


def _read_events(path: Path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_action_span_writes_start_finish_when_env_exists(tmp_path, monkeypatch):
    monkeypatch.setenv("SAFELOOP_RUN_DIR", str(tmp_path))
    monkeypatch.setenv("SAFELOOP_RUN_ID", "run-test")

    with action_span("update_billing_logic", intent="modify refund behavior"):
        pass

    events = _read_events(tmp_path / "action-events.jsonl")
    assert [e["event_type"] for e in events] == ["action_started", "action_finished"]
    assert events[0]["action_id"] == events[1]["action_id"]
    assert events[0]["name"] == "update_billing_logic"
    assert events[0]["intent"] == "modify refund behavior"
    assert events[0]["run_id"] == "run-test"
    assert verify_action_events(tmp_path / "action-events.jsonl")["status"] == "valid"


def test_action_events_valid_hash_chain_and_malformed_chain_detected(tmp_path, monkeypatch):
    monkeypatch.setenv("SAFELOOP_RUN_DIR", str(tmp_path))
    monkeypatch.setenv("SAFELOOP_RUN_ID", "run-test")
    with action_span("one", intent="intent"):
        pass
    path = tmp_path / "action-events.jsonl"
    assert verify_action_events(path)["status"] == "valid"

    lines = path.read_text().splitlines()
    event = json.loads(lines[1])
    event["prev_event_hash"] = "sha256:" + "0" * 64
    lines[1] = json.dumps(event, sort_keys=True)
    path.write_text("\n".join(lines) + "\n")
    result = verify_action_events(path)
    assert result["status"] == "invalid"
    assert any("prev hash mismatch" in issue for issue in result["issues"])


def test_verify_action_events_detects_missing_finish_where_possible(tmp_path, monkeypatch):
    monkeypatch.setenv("SAFELOOP_RUN_DIR", str(tmp_path))
    monkeypatch.setenv("SAFELOOP_RUN_ID", "run-test")
    try:
        with action_span("boom", intent="intent"):
            raise RuntimeError("x")
    except RuntimeError:
        pass
    path = tmp_path / "action-events.jsonl"
    events = _read_events(path)
    path.write_text(json.dumps(events[0], sort_keys=True) + "\n")

    result = verify_action_events(path)
    assert result["status"] == "invalid"
    assert any("missing finish" in issue for issue in result["issues"])


def test_watch_run_passes_safeloop_run_env(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    script = repo / "env_writer.py"
    script.write_text(
        "import os, pathlib\n"
        "pathlib.Path('env.json').write_text(__import__('json').dumps({"
        "'run_dir': os.environ.get('SAFELOOP_RUN_DIR'), "
        "'run_id': os.environ.get('SAFELOOP_RUN_ID')}))\n"
    )

    code, run_dir = watch_run("task", repo, [sys.executable, str(script)], run_root=tmp_path / "runs", debounce_ms=10)
    assert code == 0
    env = json.loads((repo / "env.json").read_text())
    assert env["run_dir"] == str(run_dir)
    assert env["run_id"] == run_dir.name


def test_action_span_inside_watched_agent_creates_action_events_and_verify_includes_it(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    script = repo / "agent.py"
    src_path = str(Path(__file__).resolve().parents[1] / "src")
    script.write_text(
        "import sys\n"
        f"sys.path.insert(0, {src_path!r})\n"
        "from safeloop import action_span\n"
        "with action_span('edit_file', intent='write demo'):\n"
        "    open('demo.txt', 'w').write('hello')\n"
    )

    code, run_dir = watch_run("task", repo, [sys.executable, str(script)], run_root=tmp_path / "runs", debounce_ms=10)
    assert code == 0
    action_path = run_dir / "action-events.jsonl"
    assert action_path.exists()
    result = verify_run(run_dir)
    assert result["status"] == "valid"
    assert "action-events.jsonl" in result["checked_artifacts"]


def test_action_span_correlates_runtime_firewall_preflight(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    script = repo / "agent.py"
    src_path = str(Path(__file__).resolve().parents[1] / "src")
    script.write_text(
        "import sys\n"
        f"sys.path.insert(0, {src_path!r})\n"
        "from safeloop import action_span, firewall_preflight\n"
        "with action_span('inspect_docs', intent='read docs'):\n"
        "    firewall_preflight(tool='rg', action='search', target='README.md', reason='inspect docs')\n"
    )

    code, run_dir = watch_run("task", repo, [sys.executable, str(script)], run_root=tmp_path / "runs", debounce_ms=10)
    assert code == 0
    action_events = _read_events(run_dir / "action-events.jsonl")
    firewall_events = read_runtime_tool_firewall_events(run_dir)
    assert len(firewall_events) == 1
    assert firewall_events[0]["action_id"] == action_events[0]["action_id"]
    assert firewall_events[0]["source"] == "runtime_helper"
    assert verify_run(run_dir)["status"] == "valid"


def test_verify_artifacts_validates_action_events_if_present(tmp_path, monkeypatch):
    monkeypatch.setenv("SAFELOOP_RUN_DIR", str(tmp_path))
    monkeypatch.setenv("SAFELOOP_RUN_ID", "run-test")
    with action_span("one", intent="intent"):
        pass
    assert verify_action_events(tmp_path / "action-events.jsonl")["status"] == "valid"


def test_action_span_without_run_dir_noop_does_not_create_files(tmp_path, monkeypatch):
    monkeypatch.delenv("SAFELOOP_RUN_DIR", raising=False)
    monkeypatch.delenv("SAFELOOP_RUN_ID", raising=False)
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        with action_span("noop", intent="no env"):
            pass
    finally:
        os.chdir(cwd)
    assert not list(tmp_path.iterdir())
