#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "public-mvp-readiness.md"
PYPROJECT = ROOT / "pyproject.toml"
CLI = ROOT / "src" / "safeloop" / "cli.py"

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
    missing_verifiers = [name for name in demo_verifiers if name not in cli_text]
    if missing_verifiers:
        failures.append("missing demo verifier CLI(s): " + ", ".join(missing_verifiers))

    version = project_version()
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        failures.append(f"project version is not semver x.y.z: {version}")

    messages.append(f"version={version}")
    messages.append("demo-verifier=present" if not missing_verifiers else "demo-verifier=missing")
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
