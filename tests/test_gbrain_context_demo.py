from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "gbrain-integration-demo.md"
SCRIPT = ROOT / "examples" / "gbrain_context_demo.sh"
README = ROOT / "README.md"
SPEC = ROOT / "docs" / "specs" / "operator-packet-v1.md"

OPERATOR_PACKET_V1_REQUIRED = [
    "## Operator packet v1 metadata",
    "Schema: safeloop.operator-packet.v1",
    "Run directory:",
    "Run ID:",
    "Checkpoint ID:",
    "Scope:",
    "## Summary",
    "## Action and evidence",
    "## Compensation and recovery options",
    "## Manual review decision",
    "## Non-goals and boundary",
    "Gbrain role: retrieved context evidence only",
    "SafeLoop role: action evidence, rollback plan, manual review",
    "covered local repository file changes",
    "actions outside the local repo require manual review or separate compensation",
]


def test_gbrain_demo_docs_define_role_split_and_boundaries() -> None:
    text = DOC.read_text(encoding="utf-8")
    required = [
        "Gbrain provides retrieved context/knowledge evidence",
        "agent decides and acts",
        "SafeLoop records action, evidence, rollback, and manual review",
        "Gbrain is not the scheduler or control plane",
        "does not read or write `~/.gbrain`",
        "local mock fixture",
    ]
    for phrase in required:
        assert phrase in text


def test_readme_links_gbrain_demo() -> None:
    assert "docs/gbrain-integration-demo.md" in README.read_text(encoding="utf-8")


def test_operator_packet_v1_spec_and_demo_template_define_required_handoff_fields() -> None:
    spec = SPEC.read_text(encoding="utf-8")
    script = SCRIPT.read_text(encoding="utf-8")
    docs = DOC.read_text(encoding="utf-8")

    for phrase in [
        "Operator packet v1",
        "not a scheduler, approval service, control plane, or external rollback mechanism",
        "Actions outside the local repository require manual review or separate compensation",
    ]:
        assert phrase in spec

    for required in OPERATOR_PACKET_V1_REQUIRED:
        assert required in script

    assert "operator packet v1" in docs
    assert "actions outside the local repo remain manual-review or separate-compensation territory" in docs


def test_gbrain_context_demo_runs_offline_and_preserves_real_gbrain_home(tmp_path) -> None:
    fake_home = tmp_path / "home"
    real_gbrain = fake_home / ".gbrain"
    real_gbrain.mkdir(parents=True)
    sentinel = real_gbrain / "production.sqlite"
    sentinel.write_text("do not touch\n", encoding="utf-8")

    env = os.environ.copy()
    env.update({"HOME": str(fake_home), "TMPDIR": str(tmp_path), "NO_NETWORK": "1", "PYTHON": sys.executable})
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert sentinel.read_text(encoding="utf-8") == "do not touch\n"
    assert sorted(p.name for p in real_gbrain.iterdir()) == ["production.sqlite"]

    marker = "Demo workspace retained at: "
    workspace_line = next(line for line in result.stdout.splitlines() if line.startswith(marker))
    demo_root = Path(workspace_line.removeprefix(marker))
    run_dir = Path((demo_root / "run-dir.txt").read_text(encoding="utf-8").strip())
    repo = demo_root / "repo"

    for rel in [
        "retrieved_context.json",
        "retrieved_context.brief.md",
        "operator-packet.md",
        "rollback-plan.json",
        "review-summary.json",
        "gbrain-mock/README.md",
    ]:
        assert (demo_root / rel).exists() or (run_dir / rel).exists(), rel

    copied_context = json.loads((run_dir / "retrieved_context.json").read_text(encoding="utf-8"))
    assert copied_context["source"] == "local mock fixture"
    assert copied_context["boundaries"]["gbrain_control_plane"] is False
    assert copied_context["boundaries"]["scheduler"] is False

    packet = (demo_root / "operator-packet.md").read_text(encoding="utf-8")
    for required in OPERATOR_PACKET_V1_REQUIRED:
        assert required in packet
    assert str(run_dir) in packet

    assert (repo / "service.md").read_text(encoding="utf-8") == "base service contract\n"
    applied = json.loads((run_dir / "rollback-result.json").read_text(encoding="utf-8"))
    assert applied["status"] == "applied"
