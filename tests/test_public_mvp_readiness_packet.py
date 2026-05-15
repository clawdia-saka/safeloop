from __future__ import annotations

import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "public-mvp-readiness.md"
SCRIPT = ROOT / "scripts" / "public_readiness.py"


def test_public_readiness_doc_has_release_boundary_and_local_evidence_gate() -> None:
    text = DOC.read_text(encoding="utf-8")

    required_markers = [
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
    ]
    for marker in required_markers:
        assert marker in text

    assert "safeloop verify-artifacts" in text
    assert "safeloop verify-anchor" in text
    assert "safeloop audit-control-plane-anchors" in text
    assert "python scripts/public_readiness.py --check" in text


def test_public_readiness_doc_does_not_publish_overclaims() -> None:
    text = DOC.read_text(encoding="utf-8").lower()
    banned = [
        r"(?<!not )tamper-proof",
        r"(?<!not )production governance",
        r"(?<!not a )time-machine",
        r"(?<!not a )time machine",
    ]
    for pattern in banned:
        assert re.search(pattern, text) is None, pattern


def test_public_readiness_script_check_verifies_packet_and_build_metadata() -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    output = proc.stdout
    assert "public-readiness: ok" in output
    assert "version=0.1.4" in output
    assert "demo-verifier=present" in output
    assert "demo-verifier-help=ok" in output
    assert "release-tag=not-created" in output
    assert "html-artifacts=synced" in output


def test_readme_release_boundary_matches_current_public_readiness_version() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    packet = DOC.read_text(encoding="utf-8")

    assert "SafeLoop 0.1.4" in readme
    assert "SafeLoop 0.1.4" in packet
    assert "0.0.4 reliability sprint" not in readme
    assert "no HTTP dashboard v2 in 0.0.3" not in readme
