#!/usr/bin/env python3
"""Local-only coding-agent SafeLoop example fixture."""
from __future__ import annotations

import argparse
import difflib
import json
import subprocess
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path(".safeloop-real-world/coding-agent-run"))
    args = parser.parse_args()
    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)

    before = 'def greet(name: str) -> str:\n    return f"hello {name}"\n'
    after = 'def greet(name: str) -> str:\n    cleaned = name.strip().title()\n    return f"hello {cleaned}"\n'
    test = "from sample_app import greet\n\ndef test_greet_formats_name():\n    assert greet(' ada ') == 'hello Ada'\n"

    app = out / "sample_app.py"
    test_file = out / "test_sample_app.py"
    app.write_text(before)
    (out / "before.txt").write_text(before)
    app.write_text(after)
    (out / "after.txt").write_text(after)
    test_file.write_text(test)

    diff = "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), fromfile="sample_app.py.before", tofile="sample_app.py.after"))
    (out / "diff.patch").write_text(diff)

    result = subprocess.run([sys.executable, "-m", "pytest", "-q", str(test_file)], cwd=out, text=True, capture_output=True)
    (out / "test-output.txt").write_text(result.stdout + result.stderr)

    rollback_plan = {
        "schema_version": "example-rollback-plan.v1",
        "type": "restore_original_file_contents",
        "covered_files": ["sample_app.py"],
        "exact_rollback": True,
        "operator_steps": ["Review diff.patch", "Restore before.txt to sample_app.py", "Re-run test-output command"],
    }
    (out / "rollback-plan.json").write_text(json.dumps(rollback_plan, indent=2) + "\n")

    summary = {
        "scenario": "coding_agent_run",
        "artifact_dir": str(out),
        "network": "disabled/not used",
        "exact_rollback": True,
        "artifacts": {"changed_file": str(app), "diff": str(out / "diff.patch")},
        "test_evidence": {"command": f"{sys.executable} -m pytest -q {test_file.name}", "passed": result.returncode == 0, "output": "test-output.txt"},
        "rollback_plan": rollback_plan,
    }
    (out / "run-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary))
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
