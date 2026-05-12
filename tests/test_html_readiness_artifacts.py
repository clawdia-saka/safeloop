from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from safeloop.html_artifacts import BOUNDARY_NOTES, render_docs_packet_html, render_markdown_doc_html, render_readiness_html, write_docs_packet_html, write_readiness_html


def test_render_readiness_html_includes_boundary_language_and_local_artifacts(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "run.json").write_text(json.dumps({"run_id": "run-1", "task_id": "demo", "status": "complete"}), encoding="utf-8")
    (run_dir / "rollback-plan.json").write_text(
        json.dumps({
            "operation": "rollback_selected_files",
            "external_side_effects": {"classification": "manual_review", "exact_rollback": False},
        }),
        encoding="utf-8",
    )

    html = render_readiness_html(run_dir)

    assert "<!doctype html>" in html
    assert "run-1" in html
    assert "rollback_selected_files" in html
    assert "manual_review" in html
    assert "no external network resources" in html
    for note in BOUNDARY_NOTES:
        assert note in html
    assert "Exact rollback is only claimed for covered local file changes." in html
    assert "not tamper-proof" in html
    assert "does not claim a remote transparency log" in html


def test_write_readiness_html_and_cli(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "run.json").write_text(json.dumps({"run_id": "run-cli", "task_id": "demo", "status": "ok"}), encoding="utf-8")

    output = write_readiness_html(run_dir)
    assert output.exists()
    assert "run-cli" in output.read_text(encoding="utf-8")

    cli_output = tmp_path / "report.html"
    proc = subprocess.run(
        [sys.executable, "-m", "safeloop.cli", "html-report", str(run_dir), "--output", str(cli_output)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert cli_output.exists()
    text = cli_output.read_text(encoding="utf-8")
    assert "External side effects require compensation/manual review and are not exact rollback." in text
    assert "Local artifacts are tamper-evident review aids, not tamper-proof guarantees." in text


def test_render_markdown_doc_html_keeps_markdown_canonical_and_boundaries(tmp_path: Path) -> None:
    doc = tmp_path / "public-mvp-readiness.md"
    doc.write_text("# Readiness\n\nExact rollback only for covered local files.\n", encoding="utf-8")
    html = render_markdown_doc_html(doc)
    assert "canonical source remains Markdown" in html
    assert "Exact rollback only for covered local files" in html
    for note in BOUNDARY_NOTES:
        assert note in html


def test_docs_packet_includes_readiness_schema_demo_and_case_studies() -> None:
    repo = Path.cwd()
    html = render_docs_packet_html(repo)
    assert "docs/public-mvp-readiness.md" in html
    assert "docs/specs/state-machine-and-journal-schema.md" in html
    assert "examples/rollback_selective_demo.sh" in html
    assert "docs/case-studies/github-pr-demo.md" in html
    assert "docs/case-studies/boundary-scenarios.md" in html
    assert "External side effects require compensation/manual review" in html
    assert "not tamper-proof" in html


def test_write_docs_packet_and_cli(tmp_path: Path) -> None:
    output = tmp_path / "packet.html"
    written = write_docs_packet_html(Path.cwd(), output)
    assert written == output
    assert "SafeLoop docs/demo HTML packet" in output.read_text(encoding="utf-8")
    cli_output = tmp_path / "cli-packet.html"
    proc = subprocess.run([sys.executable, "-m", "safeloop.cli", "html-docs-packet", ".", "--output", str(cli_output)], text=True, capture_output=True, check=False)
    assert proc.returncode == 0, proc.stderr
    assert "examples/rollback_selective_demo.sh" in cli_output.read_text(encoding="utf-8")
