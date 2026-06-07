from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run_cli(*args: str, cwd: Path = ROOT, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "safeloop.cli", *args],
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def test_demo_command_generates_operator_packet_manifest_and_summary(tmp_path: Path) -> None:
    output_dir = tmp_path / "demo"

    result = run_cli("demo", "--output-dir", str(output_dir), "--json")

    assert result.returncode == 0, result.stdout + result.stderr
    summary = json.loads(result.stdout)
    run_dir = Path(summary["run_dir"])
    assert summary["schema_version"] == "safeloop.demo.v1"
    assert summary["status"] == "ok"
    assert Path(summary["operator_packet"]).exists()
    assert Path(summary["operator_packet_manifest"]).exists()
    assert (run_dir / "operator-packet-v2.md").exists()
    assert (run_dir / "operator-packet-manifest.json").exists()
    assert (output_dir / "demo-summary.json").exists()
    assert summary["external_effects"]["exact_rollback"] is False

    verify = run_cli("verify-artifacts", str(run_dir))
    packet_verify = run_cli("operator-packet-verify", str(run_dir))
    assert verify.returncode == 0, verify.stdout + verify.stderr
    assert packet_verify.returncode == 0, packet_verify.stdout + packet_verify.stderr


def test_doctor_json_reports_cli_and_github_action_packet_upload() -> None:
    result = run_cli("doctor", "--repo", str(ROOT), "--json")

    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["schema_version"] == "safeloop.doctor.v1"
    assert report["status"] in {"ok", "warnings"}
    assert report["checks"]["cli"]["status"] == "ok"
    assert report["checks"]["github_action_packet_upload"]["status"] == "ok"
    assert report["checks"]["github_action_packet_upload"]["artifact_name"] == "safeloop-packet-demo"
    assert report["checks"]["github_action_packet_upload"]["verifier_command"] == "safeloop operator-packet-verify"


def test_doctor_strict_json_runs_live_demo_verify_packet_and_readiness() -> None:
    result = run_cli("doctor", "--repo", str(ROOT), "--strict", "--json", timeout=120)

    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["schema_version"] == "safeloop.doctor.v1"
    assert report["status"] == "ok"
    assert report["checks"]["strict_health"]["status"] == "ok"
    strict = report["strict"]
    assert strict["schema_version"] == "safeloop.health.v1"
    assert strict["status"] == "ok"
    assert strict["gates"]["demo"]["status"] == "ok"
    assert strict["gates"]["verify_artifacts"]["status"] == "ok"
    assert strict["gates"]["operator_packet_verify"]["status"] == "ok"
    assert strict["gates"]["public_readiness"]["status"] == "ok"
    assert Path(strict["run_dir"]).name.startswith("run-")


def test_health_json_runs_strict_gates_without_source_checkout(tmp_path: Path) -> None:
    result = run_cli("health", "--repo", str(tmp_path), "--json", timeout=120)

    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["schema_version"] == "safeloop.health.v1"
    assert report["status"] == "ok"
    assert report["gates"]["demo"]["status"] == "ok"
    assert report["gates"]["verify_artifacts"]["status"] == "ok"
    assert report["gates"]["operator_packet_verify"]["status"] == "ok"
    assert report["gates"]["public_readiness"]["status"] == "skipped"


def test_health_text_reports_failed_gate_with_nonzero_exit(tmp_path: Path) -> None:
    bad_repo = tmp_path / "not-a-directory"
    bad_repo.write_text("not a directory\n", encoding="utf-8")

    result = run_cli("health", "--repo", str(bad_repo), timeout=120)

    assert result.returncode != 0
    assert "SafeLoop health" in result.stdout
    assert "[error] demo:" in result.stdout


def test_init_agent_codex_writes_local_config_and_agent_instructions(tmp_path: Path) -> None:
    result = run_cli("init", "--agent", "codex", "--repo", str(tmp_path))

    assert result.returncode == 0, result.stdout + result.stderr
    config = json.loads((tmp_path / ".safeloop" / "config.json").read_text(encoding="utf-8"))
    instructions = (tmp_path / ".safeloop" / "AGENTS.codex.md").read_text(encoding="utf-8")
    assert config["schema_version"] == "safeloop.init.v1"
    assert config["agent"] == "codex"
    assert config["packet_dir"] == ".safeloop/packets"
    assert "safeloop demo" in instructions
    assert "safeloop doctor" in instructions
    assert "operator-packet-manifest.json" in instructions


def test_init_refuses_to_overwrite_changed_files_without_force(tmp_path: Path) -> None:
    first = run_cli("init", "--agent", "codex", "--repo", str(tmp_path))
    assert first.returncode == 0, first.stdout + first.stderr
    config_path = tmp_path / ".safeloop" / "config.json"
    config_path.write_text("manual edit\n", encoding="utf-8")

    second = run_cli("init", "--agent", "codex", "--repo", str(tmp_path))

    assert second.returncode == 1
    assert "would overwrite" in second.stderr
