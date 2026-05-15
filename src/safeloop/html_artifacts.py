from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any

BOUNDARY_NOTES = [
    "Exact rollback is only claimed for covered local file changes.",
    "External side effects require compensation/manual review and are not exact rollback.",
    "External side-effect statuses such as best_effort or verified are review labels, not exact rollback guarantees.",
    "Local artifacts are tamper-evident review aids, not tamper-proof guarantees.",
    "SafeLoop does not claim a remote transparency log unless one is explicitly implemented and configured.",
]

PUBLIC_HTML_ARTIFACTS = [
    ("docs/public-mvp-readiness.md", "docs/public-mvp-readiness.html", "SafeLoop Public MVP Readiness Packet"),
    ("docs/specs/state-machine-and-journal-schema.md", "docs/specs/state-machine-and-journal-schema.html", "SafeLoop State Machine and Journal Schema"),
    ("docs/rollback.md", "docs/rollback-demo.html", "SafeLoop Rollback Demo Review Artifact"),
    ("docs/html-readiness-artifacts.md", "docs/case-studies/rollback-html-artifacts.html", "SafeLoop Rollback Case Study HTML Artifacts"),
]


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _pretty(value: Any) -> str:
    return html.escape(json.dumps(value, indent=2, sort_keys=True, default=str))


def _artifact_rows(run_dir: Path) -> list[tuple[str, dict[str, Any]]]:
    names = ["run.json", "review-summary.json", "rollback-plan.json", "rollback-result.json", "policy-rollback-suggestion.json", "local-anchor.json"]
    rows: list[tuple[str, dict[str, Any]]] = []
    for name in names:
        data = _read_json(run_dir / name)
        if data is not None:
            rows.append((name, data))
    for path in sorted((run_dir / "checkpoints").glob("cp-*/*.json")) if (run_dir / "checkpoints").exists() else []:
        if path.name in {"restore-manifest.json", "undo-preflight.json", "rollback-result.json", "hunk-manifest.json"}:
            data = _read_json(path)
            if data is not None:
                rows.append((str(path.relative_to(run_dir)), data))
    return rows


def _inline_markdown(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    return escaped


def markdown_to_review_html(markdown: str) -> str:
    """Small, dependency-free Markdown subset renderer for committed review artifacts.

    The Markdown files remain canonical; generated HTML is intentionally a human-review
    artifact and only supports the headings/lists/fences used by SafeLoop docs.
    """
    out: list[str] = []
    in_code = False
    code_lines: list[str] = []
    list_open = False
    paragraph: list[str] = []

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            out.append("<p>" + _inline_markdown(" ".join(paragraph)) + "</p>")
            paragraph = []

    def close_list() -> None:
        nonlocal list_open
        if list_open:
            out.append("</ul>")
            list_open = False

    for line in markdown.splitlines():
        if line.startswith("```"):
            if in_code:
                out.append("<pre><code>" + html.escape("\n".join(code_lines)) + "</code></pre>")
                code_lines = []
                in_code = False
            else:
                flush_paragraph(); close_list(); in_code = True; code_lines = []
            continue
        if in_code:
            code_lines.append(line)
            continue
        if not line.strip():
            flush_paragraph(); close_list(); continue
        if line.startswith("#"):
            flush_paragraph(); close_list()
            level = min(len(line) - len(line.lstrip("#")), 6)
            title = line[level:].strip()
            out.append(f"<h{level}>{_inline_markdown(title)}</h{level}>")
            continue
        if line.startswith("- ") or line.startswith("- ["):
            flush_paragraph()
            if not list_open:
                out.append("<ul>"); list_open = True
            out.append("<li>" + _inline_markdown(line[2:].strip()) + "</li>")
            continue
        paragraph.append(line.strip())
    flush_paragraph(); close_list()
    if in_code:
        out.append("<pre><code>" + html.escape("\n".join(code_lines)) + "</code></pre>")
    return "\n".join(out)


def review_html_document(title: str, body_html: str, source_path: str) -> str:
    boundary_items = "\n".join(f"<li>{html.escape(note)}</li>" for note in BOUNDARY_NOTES)
    return f"""<!doctype html>
<html lang=\"en\">
<head>
<meta charset=\"utf-8\">
<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
<title>{html.escape(title)}</title>
<style>
body {{ font-family: system-ui, -apple-system, Segoe UI, sans-serif; margin: 2rem; color: #172033; background: #f7f8fb; }}
main {{ max-width: 1040px; margin: 0 auto; }}
.card, article {{ background: white; border: 1px solid #d8deea; border-radius: 12px; padding: 1rem 1.25rem; margin: 1rem 0; box-shadow: 0 1px 2px rgba(20,30,50,.04); }}
.badge {{ display: inline-block; background: #eaf2ff; color: #123e73; border-radius: 999px; padding: .2rem .55rem; font-size: .85rem; }}
pre {{ overflow-x: auto; background: #0f172a; color: #e2e8f0; padding: 1rem; border-radius: 8px; }}
code {{ background: #eef2f7; padding: .05rem .25rem; border-radius: 4px; }}
pre code {{ background: transparent; padding: 0; }}
.boundary {{ border-left: 5px solid #b7791f; }}
</style>
</head>
<body><main>
<p class=\"badge\">generated from canonical Markdown: {html.escape(source_path)}</p>
<section class=\"card boundary\"><h2>Claim boundaries</h2><ul>{boundary_items}</ul></section>
<article>
{body_html}
</article>
</main></body></html>
"""


def render_markdown_file_html(source: Path, *, title: str | None = None) -> str:
    markdown = source.read_text(encoding="utf-8")
    first_heading = next((line.lstrip("# ").strip() for line in markdown.splitlines() if line.startswith("# ")), source.stem)
    return review_html_document(title or first_heading, markdown_to_review_html(markdown), str(source))


def write_public_html_artifacts(root: Path) -> list[Path]:
    written: list[Path] = []
    for src_rel, dst_rel, title in PUBLIC_HTML_ARTIFACTS:
        src = root / src_rel
        dst = root / dst_rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(render_markdown_file_html(src, title=title), encoding="utf-8")
        written.append(dst)
    return written


def render_readiness_html(run_dir: Path, *, title: str = "SafeLoop readiness rollback demo report") -> str:
    """Render a self-contained local HTML report from existing SafeLoop artifacts."""
    run_dir = run_dir.resolve()
    run = _read_json(run_dir / "run.json") or {}
    rows = _artifact_rows(run_dir)
    status = html.escape(str(run.get("status", "unknown")))
    run_id = html.escape(str(run.get("run_id", "unknown")))
    task_id = html.escape(str(run.get("task_id", "unknown")))
    artifact_sections = "\n".join(
        f"<section class='artifact'><h3>{html.escape(name)}</h3><pre>{_pretty(data)}</pre></section>" for name, data in rows
    ) or "<p>No JSON artifacts found yet. Run SafeLoop demo commands first.</p>"
    return review_html_document(
        title,
        f"""<h1>{html.escape(title)}</h1>
<p class=\"badge\">self-contained local HTML artifact; no external network resources</p>
<section class=\"card\"><div><strong>Run id</strong><br>{run_id}</div><div><strong>Task id</strong><br>{task_id}</div><div><strong>Status</strong><br>{status}</div><div><strong>Run dir</strong><br>{html.escape(str(run_dir))}</div></section>
<section class=\"card\"><h2>Local artifacts included</h2><p>These embedded JSON snapshots are copied from the local run directory at render time. Verify with <code>safeloop verify-artifacts</code> and <code>safeloop verify-anchor</code> where applicable.</p></section>
{artifact_sections}""",
        str(run_dir),
    )


def write_readiness_html(run_dir: Path, output: Path | None = None) -> Path:
    output = output or (run_dir / "safeloop-readiness-report.html")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_readiness_html(run_dir), encoding="utf-8")
    return output
