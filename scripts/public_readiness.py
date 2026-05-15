#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from safeloop.html_artifacts import PUBLIC_HTML_ARTIFACTS, render_markdown_file_html

DOC = ROOT / "docs" / "public-mvp-readiness.md"
PYPROJECT = ROOT / "pyproject.toml"
CLI = ROOT / "src" / "safeloop" / "cli.py"
DEMO = ROOT / "examples" / "rollback_selective_demo.sh"

REQUIRED_DOC_MARKERS = [
    "# SafeLoop Public MVP Readiness Packet",
    "## Release boundary",
    "## Local evidence gate",
    "## Demo verifier presence",
    "## Version and build gates",
    "## Claim boundary / banned public overclaims",
    "tamper-evident review aid",
    "not a release tag",
    "not tamper-proof",
    "not production governance",
    "not a time-machine",
    "safeloop verify-artifacts",
    "safeloop verify-anchor",
    "safeloop audit-control-plane-anchors",
    "python scripts/public_readiness.py --check",
]

BANNED_PUBLIC_OVERCLAIMS = [
    ("tamper-proof", re.compile(r"(?<!not )tamper-proof", re.IGNORECASE)),
    ("production governance", re.compile(r"(?<!not )production governance", re.IGNORECASE)),
    ("time-machine", re.compile(r"(?<!not a )time-machine", re.IGNORECASE)),
    ("time machine", re.compile(r"(?<!not a )time machine", re.IGNORECASE)),
]


def project_version() -> str:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def check_public_html_artifacts() -> list[str]:
    failures: list[str] = []
    for src_rel, dst_rel, title in PUBLIC_HTML_ARTIFACTS:
        src = ROOT / src_rel
        dst = ROOT / dst_rel
        if not src.exists():
            failures.append(f"missing HTML artifact source: {src_rel}")
            continue
        expected = render_markdown_file_html(src, title=title)
        if not dst.exists():
            failures.append(f"missing HTML artifact: {dst_rel}")
            continue
        actual = dst.read_text(encoding="utf-8")
        if actual != expected:
            failures.append(f"stale HTML artifact: {dst_rel}")
    return failures


def check() -> tuple[int, list[str]]:
    messages: list[str] = []
    failures: list[str] = []

    if not DOC.exists():
        failures.append(f"missing readiness doc: {DOC.relative_to(ROOT)}")
        doc_text = ""
    else:
        doc_text = DOC.read_text(encoding="utf-8")
        for marker in REQUIRED_DOC_MARKERS:
            if marker not in doc_text:
                failures.append(f"missing doc marker: {marker}")
        for label, pattern in BANNED_PUBLIC_OVERCLAIMS:
            match = pattern.search(doc_text)
            if match:
                failures.append(f"public overclaim not boundary-qualified: {label}")

    cli_text = CLI.read_text(encoding="utf-8")
    demo_verifiers = ["verify-artifacts", "verify-anchor", "audit-control-plane-anchors"]
    readiness_commands = ["review", "explain", "policy-check", "rollback"]
    missing_verifiers = [name for name in demo_verifiers if name not in cli_text]
    missing_commands = [name for name in readiness_commands if f'add_parser("{name}")' not in cli_text]
    cli_help_failures: list[str] = []
    for name in [*demo_verifiers, *readiness_commands]:
        proc = subprocess.run(
            [sys.executable, "-m", "safeloop.cli", name, "--help"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            cli_help_failures.append(name)
    if missing_verifiers:
        failures.append("missing demo verifier CLI(s): " + ", ".join(missing_verifiers))
    if missing_commands:
        failures.append("missing readiness CLI(s): " + ", ".join(missing_commands))
    if cli_help_failures:
        failures.append("readiness CLI help failed: " + ", ".join(cli_help_failures))
    if not DEMO.exists():
        failures.append(f"missing demo script: {DEMO.relative_to(ROOT)}")
    else:
        demo_text = DEMO.read_text(encoding="utf-8")
        for marker in ["watch --loop", "rollback plan", "--to-start", "--files", "--hunks", "policy-check"]:
            if marker not in demo_text:
                failures.append(f"demo script missing marker: {marker}")
        if "compens" in cli_text and "compens" not in demo_text:
            failures.append("compensation CLI appears present but demo lacks compensation/manual-review hook")

    html_artifact_failures = check_public_html_artifacts()
    failures.extend(html_artifact_failures)

    version = project_version()
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        failures.append(f"project version is not semver x.y.z: {version}")

    messages.append(f"version={version}")
    messages.append("demo-verifier=present" if not missing_verifiers else "demo-verifier=missing")
    messages.append("readiness-cli=present" if not missing_commands else "readiness-cli=missing")
    messages.append("demo-script=present" if DEMO.exists() else "demo-script=missing")
    messages.append("demo-verifier-help=ok" if not cli_help_failures else "demo-verifier-help=failed")
    messages.append("html-artifacts=synced" if not html_artifact_failures else "html-artifacts=out-of-sync")
    messages.append("release-tag=not-created")
    messages.append("build-gate=pyproject-present")

    if failures:
        messages.extend(f"FAIL: {failure}" for failure in failures)
        return 1, messages
    messages.insert(0, "public-readiness: ok")
    return 0, messages


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check the local public MVP readiness packet without tagging a release.")
    parser.add_argument("--check", action="store_true", help="verify docs/local evidence gate markers")
    args = parser.parse_args(argv)
    if not args.check:
        parser.print_help()
        return 2
    code, messages = check()
    print("\n".join(messages))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
