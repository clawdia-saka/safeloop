from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_compensation_doc_exists_and_pins_external_boundary() -> None:
    text = (ROOT / "docs" / "compensation.md").read_text(encoding="utf-8")

    required = [
        "Compensation is not rollback",
        "exact_rollback: false",
        "manual_review_required",
        "compensation action recorded",
        "GitHub issue/comment",
        "message correction",
        "covered local file",
        "external service",
    ]
    for marker in required:
        assert marker in text


def test_readme_links_compensation_examples_and_boundary() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "docs/compensation.md" in text
    assert "examples/compensation_github_issue_demo.sh" in text
    assert "examples/compensation_message_demo.sh" in text
    assert "External side effects are manual-review/compensation only" in text
    assert "covered local" in text


def test_github_issue_compensation_demo_records_non_exact_compensation() -> None:
    proc = subprocess.run(
        ["bash", str(ROOT / "examples" / "compensation_github_issue_demo.sh")],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    run_dir = Path(proc.stdout.strip().splitlines()[-1])
    plan = json.loads((run_dir / "compensation-plan.json").read_text(encoding="utf-8"))

    assert plan["schema_version"] == "compensation-plan.v1"
    assert plan["exact_rollback"] is False
    assert plan["items"][0]["effect_class"] == "github_issue_comment"
    assert plan["items"][0]["compensation"]["capability"] == "best_effort"
    assert plan["items"][0]["compensation"]["action"] == "close_issue_or_post_correction_comment"
    assert plan["items"][0]["compensation_action_recorded"] is True
    assert plan["status"] == "operator_review_required"
    assert "operator_review_required" in " ".join(plan["warnings"])
    assert "not exact rollback" in " ".join(plan["items"][0]["warnings"])


def test_message_compensation_demo_requires_manual_review_and_records_correction() -> None:
    proc = subprocess.run(
        ["bash", str(ROOT / "examples" / "compensation_message_demo.sh")],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    run_dir = Path(proc.stdout.strip().splitlines()[-1])
    plan = json.loads((run_dir / "compensation-plan.json").read_text(encoding="utf-8"))

    assert plan["exact_rollback"] is False
    assert plan["items"][0]["effect_class"] == "message_send"
    assert plan["items"][0]["compensation"]["capability"] == "manual"
    assert plan["items"][0]["compensation"]["action"] == "delete_if_supported_else_send_correction"
    assert plan["items"][0]["compensation_action_recorded"] is True
    assert "manual_review_required" in " ".join(plan["warnings"] + plan["items"][0]["warnings"])
