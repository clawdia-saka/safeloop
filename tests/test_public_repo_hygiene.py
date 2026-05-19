from pathlib import Path

import tomllib


ROOT = Path(__file__).resolve().parents[1]


def test_public_community_files_exist() -> None:
    required = [
        "LICENSE",
        "SECURITY.md",
        "CONTRIBUTING.md",
        "CODE_OF_CONDUCT.md",
        "SUPPORT.md",
        ".github/PULL_REQUEST_TEMPLATE.md",
        ".github/ISSUE_TEMPLATE/bug_report.yml",
        ".github/ISSUE_TEMPLATE/feature_request.yml",
        ".github/ISSUE_TEMPLATE/release_readiness.yml",
        ".github/ISSUE_TEMPLATE/config.yml",
    ]

    for rel_path in required:
        assert (ROOT / rel_path).exists(), rel_path


def test_license_and_security_policy_are_public_ready() -> None:
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    security_text = (ROOT / "SECURITY.md").read_text(encoding="utf-8")

    assert "MIT License" in license_text
    assert "Copyright (c) 2026 Nous Research" in license_text
    assert "GitHub private vulnerability reporting" in security_text
    assert "not claim tamper-proof storage" in security_text


def test_ci_and_release_workflows_exist_and_cover_pypi() -> None:
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    release = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    release_doc = (ROOT / "docs" / "release.md").read_text(encoding="utf-8")

    assert "python -m pytest -q" in ci
    assert "python scripts/public_readiness.py --check" in ci
    assert "python -m build" in ci
    assert "python -m twine check dist/*" in ci
    assert "safeloop demo --output-dir .safeloop/ci-demo --json" in ci
    assert "actions/upload-artifact@v4" in ci
    assert "safeloop-packet-demo" in ci
    assert "pypa/gh-action-pypi-publish@release/v1" in release
    assert "softprops/action-gh-release@v2" in release
    assert "Validate tag matches pyproject version" in release
    assert "PyPI Trusted Publishing" in release_doc
    assert "git tag -a v0.2.0" in release_doc


def test_pyproject_exposes_release_metadata() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]

    assert project["license"] == "MIT"
    assert project["license-files"] == ["LICENSE"]
    assert "dev" in project["optional-dependencies"]
