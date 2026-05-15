from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "release-notes-0.2.1-candidate.md"
README = ROOT / "README.md"


def _doc_text() -> str:
    assert DOC.exists()
    return DOC.read_text(encoding="utf-8")


def _readme() -> str:
    assert README.exists()
    return README.read_text(encoding="utf-8")

def test_v021_release_notes_candidate_file_exists() -> None:
    assert DOC.exists()


def test_v021_release_notes_candidate_includes_all_hardening_items() -> None:
    text = _doc_text()
    required = [
        "operator packet status drift",
        "install clean-env smoke",
        "external effect registry compatibility",
        "exact rollback overclaim",
        "compensation evidence required gate",
        "operator packet manifest verification",
        "public readiness release gate",
        "packet_hash_verify_design_gap",
    ]
    for phrase in required:
        assert phrase in text


def test_v021_release_notes_candidate_keeps_release_actions_on_hold() -> None:
    text = _doc_text()
    assert "release action: HOLD" in text
    assert "tag / GitHub Release / PyPI" in text
    assert "without explicit TT approval" in text
    assert "does not authorize tag creation" in text
    assert "does not authorize PyPI publish" in text


def test_v021_release_notes_candidate_boundary_avoids_overclaims() -> None:
    text = _doc_text()
    assert "exact rollback only applies to covered local file changes" in text
    assert "external actions remain exact_rollback=false" in text
    assert "external side effects are compensation/manual-review only" in text
    assert "local tamper-evident verification, not tamper-proof" in text
    assert "no hosted control plane" in text
    assert "no remote transparency log" in text
    assert "tamper-proof" in text
    assert "tamper-proof guarantee" not in text
    assert "exact rollback for external actions" not in text


def test_v021_release_notes_candidate_no_publish_commands() -> None:
    text = _doc_text()
    forbidden = [
        "git tag v0.2.1",
        "gh release create",
        "twine upload",
        "python -m twine upload",
    ]
    for phrase in forbidden:
        assert phrase not in text


def test_readme_declares_v021_candidate_hold_state() -> None:
    text = _readme()

    assert "SafeLoop v0.2.1 candidate readiness is complete." in text
    assert "Release decision is pending explicit TT approval." in text
    assert "No tag, GitHub Release, or PyPI publish should happen yet." in text
    assert "release action: HOLD" in text
    assert "docs/test-only release notes candidate" in text
    assert "No runtime behavior change" in text
    assert "No rollback/compensation semantics change" in text
