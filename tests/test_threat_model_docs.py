from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
THREAT_MODEL = ROOT / "docs" / "threat-model.md"
README = ROOT / "README.md"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_threat_model_documents_public_alpha_boundaries() -> None:
    text = _text(THREAT_MODEL)

    required_phrases = [
        "tamper-evident, not tamper-proof",
        "exact rollback only for covered local file changes",
        "verified again at apply time",
        "external actions require manual review or compensation",
        "audit artifacts are review aids, not absolute truth",
        "malicious agent",
        "compromised machine",
        "hosted attacker",
        "non-goals for the current public alpha",
    ]

    for phrase in required_phrases:
        assert phrase in text


def test_threat_model_avoids_unqualified_overclaims() -> None:
    text = _text(THREAT_MODEL)

    banned_patterns = {
        "tamper-proof": r"(?<!not )tamper-proof",
        "guaranteed rollback": r"guaranteed rollback",
        "absolute truth": r"(?<!not )absolute truth",
        "bulletproof": r"bulletproof",
        "production-ready security": r"production-ready security",
    }

    for label, pattern in banned_patterns.items():
        assert not re.search(pattern, text, re.IGNORECASE), label


def test_readme_links_to_threat_model() -> None:
    readme = _text(README)
    assert "[threat model](docs/threat-model.md)" in readme.lower()
