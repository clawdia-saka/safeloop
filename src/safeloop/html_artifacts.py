from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

BOUNDARY_NOTES = [
    "Exact rollback is only claimed for covered local file changes.",
    "External side effects require compensation/manual review and are not exact rollback.",
    "External side-effect statuses such as best_effort or verified are review labels, not exact rollback guarantees.",
    "Local artifacts are tamper-evident review aids, not tamper-proof guarantees.",
    "SafeLoop does not claim a remote transparency log unless one is explicitly implemented and configured.",
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


def render_readiness_html(run_dir: Path, *, title: str = "SafeLoop readiness rollback demo report") -> str:
    """Render a self-contained local HTML report from existing SafeLoop artifacts."""
    run_dir = run_dir.resolve()
    run = _read_json(run_dir / "run.json") or {}
    rows = _artifact_rows(run_dir)
    status = html.escape(str(run.get("status", "unknown")))
    run_id = html.escape(str(run.get("run_id", "unknown")))
    task_id = html.escape(str(run.get("task_id", "unknown")))
    boundary_items = "\n".join(f"<li>{html.escape(note)}</li>" for note in BOUNDARY_NOTES)
    artifact_sections = "\n".join(
        f"<section class='artifact'><h3>{html.escape(name)}</h3><pre>{_pretty(data)}</pre></section>" for name, data in rows
    ) or "<p>No JSON artifacts found yet. Run SafeLoop demo commands first.</p>"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
body {{ font-family: system-ui, -apple-system, Segoe UI, sans-serif; margin: 2rem; color: #172033; background: #f7f8fb; }}
main {{ max-width: 1040px; margin: 0 auto; }}
.card, .artifact {{ background: white; border: 1px solid #d8deea; border-radius: 12px; padding: 1rem 1.25rem; margin: 1rem 0; box-shadow: 0 1px 2px rgba(20,30,50,.04); }}
h1 {{ margin-bottom: .25rem; }}
.meta {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(12rem, 1fr)); gap: .75rem; }}
.badge {{ display: inline-block; background: #eaf2ff; color: #123e73; border-radius: 999px; padding: .2rem .55rem; font-size: .85rem; }}
pre {{ overflow-x: auto; background: #0f172a; color: #e2e8f0; padding: 1rem; border-radius: 8px; }}
.boundary {{ border-left: 5px solid #b7791f; }}
</style>
</head>
<body><main>
<h1>{html.escape(title)}</h1>
<p class="badge">self-contained local HTML artifact; no external network resources</p>
<section class="card meta"><div><strong>Run id</strong><br>{run_id}</div><div><strong>Task id</strong><br>{task_id}</div><div><strong>Status</strong><br>{status}</div><div><strong>Run dir</strong><br>{html.escape(str(run_dir))}</div></section>
<section class="card boundary"><h2>Claim boundaries</h2><ul>{boundary_items}</ul></section>
<section class="card"><h2>Local artifacts included</h2><p>These embedded JSON snapshots are copied from the local run directory at render time. Verify with <code>safeloop verify-artifacts</code> and <code>safeloop verify-anchor</code> where applicable.</p></section>
{artifact_sections}
</main></body></html>
"""


def write_readiness_html(run_dir: Path, output: Path | None = None) -> Path:
    output = output or (run_dir / "safeloop-readiness-report.html")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_readiness_html(run_dir), encoding="utf-8")
    return output


def _markdown_preview(path: Path, *, max_chars: int = 12000) -> str:
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if len(text) > max_chars:
        text = text[:max_chars] + "\n\n[truncated for local HTML review artifact]"
    return html.escape(text)


def render_markdown_doc_html(path: Path, *, title: str | None = None) -> str:
    """Render a canonical Markdown doc as a self-contained HTML review card."""
    title = title or path.name
    boundary_items = "\n".join(f"<li>{html.escape(note)}</li>" for note in BOUNDARY_NOTES)
    preview = _markdown_preview(path)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{html.escape(title)}</title><style>
body {{ font-family: system-ui, -apple-system, Segoe UI, sans-serif; margin: 2rem; color: #172033; background: #f7f8fb; }}
main {{ max-width: 1040px; margin: 0 auto; }} .card {{ background:white; border:1px solid #d8deea; border-radius:12px; padding:1rem 1.25rem; margin:1rem 0; }}
pre {{ white-space:pre-wrap; overflow-wrap:anywhere; background:#0f172a; color:#e2e8f0; padding:1rem; border-radius:8px; }} .badge {{ display:inline-block; background:#eaf2ff; color:#123e73; border-radius:999px; padding:.2rem .55rem; font-size:.85rem; }} .boundary {{ border-left:5px solid #b7791f; }}
</style></head><body><main><h1>{html.escape(title)}</h1><p class="badge">self-contained local HTML artifact; canonical source remains Markdown</p><section class="card boundary"><h2>Claim boundaries</h2><ul>{boundary_items}</ul></section><section class="card"><h2>Canonical Markdown preview</h2><p>Source: <code>{html.escape(str(path))}</code></p><pre>{preview}</pre></section></main></body></html>"""


def render_docs_packet_html(repo_root: Path) -> str:
    repo_root = repo_root.resolve()
    docs = [
        repo_root / "docs" / "public-mvp-readiness.md",
        repo_root / "docs" / "specs" / "state-machine-and-journal-schema.md",
        repo_root / "docs" / "case-studies" / "github-pr-demo.md",
        repo_root / "docs" / "case-studies" / "boundary-scenarios.md",
        repo_root / "examples" / "rollback_selective_demo.sh",
    ]
    sections = []
    for doc in docs:
        label = doc.relative_to(repo_root) if doc.exists() else doc
        sections.append(f"<section class='card'><h2>{html.escape(str(label))}</h2><pre>{_markdown_preview(doc) if doc.exists() else 'missing'}</pre></section>")
    boundary_items = "\n".join(f"<li>{html.escape(note)}</li>" for note in BOUNDARY_NOTES)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>SafeLoop docs/demo HTML packet</title><style>body{{font-family:system-ui,-apple-system,Segoe UI,sans-serif;margin:2rem;color:#172033;background:#f7f8fb}}main{{max-width:1120px;margin:0 auto}}.card{{background:white;border:1px solid #d8deea;border-radius:12px;padding:1rem 1.25rem;margin:1rem 0}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#0f172a;color:#e2e8f0;padding:1rem;border-radius:8px}}.boundary{{border-left:5px solid #b7791f}}</style></head><body><main><h1>SafeLoop docs/demo HTML packet</h1><p>Human-review artifact only; Markdown and shell scripts remain canonical.</p><section class="card boundary"><h2>Rollback boundary language</h2><ul>{boundary_items}</ul></section>{''.join(sections)}</main></body></html>"""


def write_markdown_doc_html(path: Path, output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_markdown_doc_html(path), encoding="utf-8")
    return output


def write_docs_packet_html(repo_root: Path, output: Path | None = None) -> Path:
    output = output or (repo_root / "docs" / "safeloop-docs-demo-packet.html")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_docs_packet_html(repo_root), encoding="utf-8")
    return output
