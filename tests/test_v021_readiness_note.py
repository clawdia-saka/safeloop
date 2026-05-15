from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOTE = ROOT / "docs" / "v0.2.1-readiness-note.md"


def _note_text() -> str:
    assert NOTE.exists(), "docs/v0.2.1-readiness-note.md must exist"
    return NOTE.read_text(encoding="utf-8")


def test_v021_readiness_note_declares_hold_release_posture() -> None:
    text = _note_text()

    assert "Ready for public-readiness messaging: YES" in text
    assert "Ready for tag / GitHub Release / PyPI: HOLD" in text
    assert "TT explicit approval required before release/publish" in text


def test_v021_readiness_note_lists_cleared_blockers_and_next_focus() -> None:
    text = _note_text()

    for blocker in [
        "operator_packet_external_status_drift",
        "install_clean_env_smoke",
        "external_effect_registry_compat",
        "exact rollback overclaim",
        "compensation_evidence_required_gate",
        "operator packet status drift",
        "clean install post-rollback verify semantics",
        "invalid external registry compensation plan masking",
        "compensation result missing receipt gate",
    ]:
        assert blocker in text
    assert "compensation_complete_verify_receipt" in text
    assert "packet_hash_verify_design_gap" in text


def test_v021_readiness_note_preserves_external_action_boundaries() -> None:
    text = _note_text()

    assert "external actions remain exact_rollback=false" in text
    assert "External side effects are compensation/manual-review only" in text
    assert "exact rollback only for covered local file changes" in text
    assert "no hosted control plane" in text
    assert "no remote transparency log" in text
    assert "no PyPI/GitHub Release/tag without explicit TT approval" in text
    assert "no exact_rollback=true promotion for external actions" in text


def test_v021_readiness_note_does_not_authorize_release_actions() -> None:
    text = _note_text().lower()

    forbidden_phrases = [
        "ready for tag: yes",
        "ready for github release: yes",
        "ready for pypi: yes",
        "authorized to create a tag",
        "authorized to publish to pypi",
        "create the v0.2.1 tag",
        "publish to pypi",
        "external actions are exact rollback",
        "exact_rollback=true for external actions",
        "external exact rollback",
    ]
    for phrase in forbidden_phrases:
        assert phrase not in text
