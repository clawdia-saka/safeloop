# Contributing to SafeLoop

Thanks for helping make long-running agent work easier to inspect and recover.
SafeLoop is intentionally careful about public claims: exact rollback is only
for covered local file changes, while external side effects require
compensation or manual review.

## Development Setup

SafeLoop requires Python 3.11 or newer.

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -U pip
python -m pip install -e ".[dev]"
```

Run the main checks:

```bash
python -m pytest -q
python scripts/public_readiness.py --check
python -m build
```

## Pull Request Guidelines

- Keep public claims recoverability-first: roll back covered local files,
  compensate or manually review external effects, and audit everything.
- Add or update tests for behavior changes.
- Update README or docs when commands, artifacts, release boundaries, or public
  claims change.
- Do not commit secrets, private run artifacts, real external-service payloads,
  or credentials.
- Prefer fake/local fixtures for demos involving GitHub, messaging, email,
  webhooks, browser actions, or other external systems.

## Useful Test Slices

```bash
python -m pytest tests/test_packaging_contract.py tests/test_public_mvp_readiness_packet.py -q
python -m pytest tests/test_agent_watchdog_rc.py tests/test_rollback_review.py -q
python -m pytest tests/test_external_effects.py tests/test_operator_packet_v2.py -q
```

## Release Changes

Release PRs should keep these surfaces in sync:

- `pyproject.toml` version
- `README.md` install and wheel smoke-test examples
- `docs/release-notes-*.md`
- `docs/public-mvp-readiness.md`
- Git tag and PyPI publication workflow expectations in `docs/release.md`

Only maintainers should create release tags or publish to PyPI.
