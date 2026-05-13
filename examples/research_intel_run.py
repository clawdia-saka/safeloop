#!/usr/bin/env python3
"""Local-only research/intel SafeLoop example fixture."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


SOURCES = [
    {"id": "local-release-note", "title": "Fixture release note", "date": "2024-01-15", "path": "sources/release-note.txt"},
    {"id": "local-incident-note", "title": "Fixture incident note", "date": "2023-11-03", "path": "sources/incident-note.txt"},
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path(".safeloop-real-world/research-intel-run"))
    args = parser.parse_args()
    out = args.output_dir
    sources_dir = out / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)

    (sources_dir / "release-note.txt").write_text("Fixture source: Widget API changed retry defaults in 2024.\n")
    (sources_dir / "incident-note.txt").write_text("Fixture source: Older outage report mentions retry amplification risk.\n")

    brief = """# Evidence brief: Widget API retry risk

Scope: local-only fixture assembled from checked-in/generated text files; no network access.

## Finding
- Retry behavior may amplify failures if the caller retries without jitter.

## Sources
- local-release-note (2024-01-15): Fixture release note.
- local-incident-note (2023-11-03): Fixture incident note.

## Confidence
STALE / LOW CONFIDENCE: these are local fixture sources, at least one is old, and no live source refresh was performed.

## SafeLoop boundary
This is evidence packaging, not a claim that external facts are current.
"""
    (out / "evidence-brief.md").write_text(brief)

    summary = {
        "scenario": "research_intel_run",
        "artifact_dir": str(out),
        "network": "disabled/not used",
        "exact_rollback": True,
        "confidence": "low",
        "stale_marker": True,
        "sources": SOURCES,
        "brief": "evidence-brief.md",
    }
    (out / "run-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
