import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = REPO_ROOT / "examples"
DOC = REPO_ROOT / "docs" / "real-world-agent-runs.md"


def _run_example(script_name: str, tmp_path: Path) -> dict:
    out_dir = tmp_path / script_name.removesuffix(".py")
    result = subprocess.run(
        [sys.executable, str(EXAMPLES / script_name), "--output-dir", str(out_dir)],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    summary = json.loads(result.stdout)
    assert summary["artifact_dir"] == str(out_dir)
    assert (out_dir / "run-summary.json").exists()
    return json.loads((out_dir / "run-summary.json").read_text())


def test_real_world_agent_run_docs_and_readme_link_examples() -> None:
    doc = DOC.read_text()
    readme = (REPO_ROOT / "README.md").read_text()

    assert "Real-world agent run examples" in doc
    for script in [
        "coding_agent_run.py",
        "research_intel_run.py",
        "browser_api_action_run.py",
    ]:
        assert f"examples/{script}" in doc
        assert f"examples/{script}" in readme
    assert "docs/real-world-agent-runs.md" in readme
    assert "not production integrations" in doc
    assert "no network" in doc.lower()


def test_coding_agent_example_generates_local_change_test_evidence_and_rollback_plan(tmp_path) -> None:
    summary = _run_example("coding_agent_run.py", tmp_path)

    assert summary["scenario"] == "coding_agent_run"
    assert summary["network"] == "disabled/not used"
    assert summary["exact_rollback"] is True
    assert summary["artifacts"]["changed_file"].endswith("sample_app.py")
    assert "pytest" in summary["test_evidence"]["command"]
    assert summary["test_evidence"]["passed"] is True
    assert summary["rollback_plan"]["type"] == "restore_original_file_contents"
    for artifact in ["before.txt", "after.txt", "diff.patch", "test-output.txt", "rollback-plan.json"]:
        assert (Path(summary["artifact_dir"]) / artifact).exists()


def test_research_intel_example_generates_evidence_brief_with_stale_low_confidence_marker(tmp_path) -> None:
    summary = _run_example("research_intel_run.py", tmp_path)

    assert summary["scenario"] == "research_intel_run"
    assert summary["network"] == "disabled/not used"
    assert summary["exact_rollback"] is True
    brief = Path(summary["artifact_dir"]) / "evidence-brief.md"
    assert brief.exists()
    brief_text = brief.read_text()
    assert "STALE / LOW CONFIDENCE" in brief_text
    assert summary["confidence"] == "low"
    assert summary["stale_marker"] is True
    assert len(summary["sources"]) >= 2


def test_browser_api_like_action_example_blocks_outside_action_and_keeps_exact_rollback_false(tmp_path) -> None:
    summary = _run_example("browser_api_action_run.py", tmp_path)

    assert summary["scenario"] == "browser_api_action_run"
    assert summary["network"] == "disabled/not used"
    assert summary["outside_action"]["status"] == "blocked_manual_review"
    assert summary["outside_action"]["exact_rollback"] is False
    assert summary["exact_rollback"] is False
    assert summary["manual_review_required"] is True
    assert (Path(summary["artifact_dir"]) / "blocked-action.json").exists()
    assert (Path(summary["artifact_dir"]) / "manual-review.md").exists()
