# SafeLoop Release Process

This document keeps tag, GitHub Release, and PyPI publication in one place.
Only maintainers should perform release steps.

## Release Boundary

SafeLoop `0.2.0` is a local-first public release. It can claim covered local
file rollback when verified preconditions hold. It must not claim exact rollback
for GitHub, messaging, email, webhooks, browser actions, databases, or other
systems outside the covered local repo.

## Preflight

Run these checks from a clean checkout:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -U pip
python -m pip install -e ".[dev]"
python -m pytest -q
python scripts/public_readiness.py --check
python -m build
python -m twine check dist/*
```

Confirm the version is synchronized:

- `pyproject.toml`
- `README.md`
- `docs/public-mvp-readiness.md`
- `docs/release-notes-0.2.0.md`
- wheel smoke-test examples

## PyPI Trusted Publishing

Configure the PyPI project `safeloop` to trust this GitHub workflow before
pushing a release tag:

- owner: `clawdia-saka`
- repository: `safeloop`
- workflow: `release.yml`
- environment: `pypi`

The release workflow uses GitHub OIDC and does not require a PyPI API token in
repository secrets.

## Tag and Publish

After the preflight passes and PyPI Trusted Publishing is configured:

```bash
git tag -a v0.2.0 -m "SafeLoop v0.2.0"
git push origin v0.2.0
```

Pushing the tag runs `.github/workflows/release.yml`, which:

1. verifies that the tag matches `pyproject.toml`,
2. runs tests and the public readiness gate,
3. builds the source distribution and wheel,
4. checks package metadata with Twine,
5. publishes to PyPI, and
6. creates a GitHub Release with the built artifacts.

## After Release

Verify the public install path:

```bash
pipx install safeloop
safeloop --version
safeloop --help
```

If the PyPI publication fails after the GitHub Release is created, fix the
workflow or PyPI Trusted Publishing configuration and rerun the failed release
job from GitHub Actions. Do not move an existing public tag unless the release
was never consumed and maintainers have explicitly agreed to replace it.
