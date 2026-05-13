from pathlib import Path


def test_recoverability_first_doc_keeps_safe_public_framing() -> None:
    doc = Path('docs/recoverability-first.md').read_text(encoding='utf-8')

    assert 'recoverability-first' in doc
    assert 'covered local file changes' in doc
    assert 'External side effects' in doc
    assert 'never treated as exact rollback' in doc
    assert 'Actions outside the local repo' in doc
    assert 'Action groups' in doc
    assert 'Manual handoff' in doc
    assert 'safeloop watch-run' in doc
    assert 'safeloop rollback plan' in doc
    assert 'safeloop rollback apply' in doc
    assert 'compatibility alias' in doc
    assert 'Framework integrations' in doc


def test_recoverability_demo_html_shows_five_command_flow_and_boundary() -> None:
    html = Path('examples/recoverability_demo.html').read_text(encoding='utf-8')

    for command in [
        'safeloop watch-run',
        'safeloop timeline',
        'safeloop verify-artifacts',
        'safeloop rollback plan',
        'safeloop rollback apply',
    ]:
        assert command in html
    assert 'Covered local file' in html
    assert 'External side effect' in html
    assert 'exact_rollback: false' in html
    assert 'watch --loop' in html
    assert 'undo' in html


def test_full_demo_script_documents_public_packet_flow() -> None:
    script = Path('examples/full_demo.sh').read_text(encoding='utf-8')
    readme = Path('README.md').read_text(encoding='utf-8')
    doc = Path('docs/recoverability-first.md').read_text(encoding='utf-8')

    for marker in [
        'watch-run',
        'timeline',
        'verify-artifacts',
        'review',
        'rollback plan',
        'operator-packet.md',
        'rollback apply',
        'public_readiness.py --check',
        'external_review_required',
        'exact_rollback=false',
        'manual review/compensation',
    ]:
        assert marker in script
    assert 'bash examples/full_demo.sh' in readme
    assert 'examples/full_demo.sh' in doc


def test_readme_links_lightweight_recoverability_gif() -> None:
    readme = Path('README.md').read_text(encoding='utf-8')
    gif_path = Path('docs/assets/safeloop-readme-demo.gif')

    assert '![SafeLoop recoverability demo](docs/assets/safeloop-readme-demo.gif)' in readme
    assert gif_path.exists()
    assert gif_path.stat().st_size < 1_000_000


def test_recoverability_external_effect_demo_script_documents_not_exact_external_rollback() -> None:
    script = Path('examples/recoverability_external_effect_demo.sh').read_text(encoding='utf-8')

    assert 'safeloop watch-run' in script
    assert 'safeloop verify-artifacts' in script
    assert 'safeloop rollback plan' in script
    assert 'safeloop rollback apply' in script
    assert 'external_review_required' in script
    assert 'manual review/compensation' in script
    assert 'Local file after rollback' in script
