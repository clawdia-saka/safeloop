from pathlib import Path


def test_readme_has_public_one_line_install_and_e2e_quickstart() -> None:
    readme = Path('README.md').read_text(encoding='utf-8')
    assert '## Quickstart' in readme
    assert 'Current source version: **SafeLoop 0.2.0**' in readme
    assert 'pipx install git+https://github.com/clawdia-saka/safeloop.git' in readme
    assert 'pipx install git+https://github.com/clawdia-saka/safeloop.git@v0.2.0' in readme
    assert 'dist/safeloop-0.2.0-py3-none-any.whl' in readme
    assert 'dist/safeloop-0.1.4-py3-none-any.whl' not in readme
    assert 'safeloop watch-run --task-id demo' in readme
    assert 'safeloop review "$RUN_DIR"' in readme
    assert 'safeloop rollback plan "$RUN_DIR" "$RUN_ID" --files note.txt' in readme
    assert 'safeloop rollback apply "$RUN_DIR" "$RUN_ID" --files note.txt' in readme
    assert 'safeloop demo' in readme
    assert 'safeloop doctor' in readme
    assert 'safeloop init --agent codex' in readme
    assert 'External side effects are manual-review/compensation only' in readme
