#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "public-mvp-readiness.md"
PYPROJECT = ROOT / "pyproject.toml"
CLI = ROOT / "src" / "safeloop" / "cli.py"
DEMO = ROOT / "examples" / "rollback_selective_demo.sh"
FULL_DEMO = ROOT / "examples" / "full_demo.sh"

REQUIRED_DOC_MARKERS = [
    "# SafeLoop Public MVP Readiness Packet",
    "## Release boundary",
    "## Local evidence gate",
    "## Demo verifier presence",
    "## Full demo flow",
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
    "bash examples/full_demo.sh",
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
    missing_commands = [
        name
        for name in readiness_commands
        if f'add_parser("{name}")' not in cli_text and f'add_parser(\n        "{name}"' not in cli_text
    ]
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

    if not FULL_DEMO.exists():
        failures.append(f"missing full demo script: {FULL_DEMO.relative_to(ROOT)}")
    else:
        full_demo_text = FULL_DEMO.read_text(encoding="utf-8")
        for marker in [
            "watch-run",
            "timeline",
            "verify-artifacts",
            "review",
            "rollback plan",
            "operator-packet.md",
            "operator-packet-v2.md",
            "rollback apply",
            "public_readiness.py --check",
            "external_review_required",
            "exact_rollback=false",
            "manual review/compensation",
        ]:
            if marker not in full_demo_text:
                failures.append(f"full demo script missing marker: {marker}")

    version = project_version()
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        failures.append(f"project version is not semver x.y.z: {version}")

    messages.append(f"version={version}")
    messages.append("demo-verifier=present" if not missing_verifiers else "demo-verifier=missing")
    messages.append("readiness-cli=present" if not missing_commands else "readiness-cli=missing")
    messages.append("demo-script=present" if DEMO.exists() else "demo-script=missing")
    messages.append("full-demo=present" if FULL_DEMO.exists() else "full-demo=missing")
    messages.append("demo-verifier-help=ok" if not cli_help_failures else "demo-verifier-help=failed")
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
