# SafeLoop v0.2.0 — release-prep candidate

Status: PR-able release preparation only. This document does **not** create a tag, publish to PyPI, or create a GitHub release.

## Release boundary

SafeLoop v0.2.0 is a local recoverability and evidence-packet release for long-running AI agent work. The release story should remain explicit:

- Exact rollback applies only to covered local file changes captured and verified by SafeLoop artifacts.
- External side effects such as GitHub, messaging, email, webhooks, deployments, payments, or hosted services require compensation and manual review.
- Local artifacts are tamper-evident review aids, not a remote transparency log and not tamper-proof infrastructure.
- Hosted multi-user control plane, production signing network, and public append-only transparency are not shipped in v0.2.0.

## Highlights

- v0.2 readiness matrix and completion-gap audit for the public local scope.
- Operator-facing timeline, explain, review, rollback plan/apply, and operator-packet evidence flow.
- Selective rollback coverage for local files/hunks and action-level review paths.
- Compensation adapter contract examples that preserve `exact_rollback=false` for non-local effects.
- Public readiness script and full-demo path for local release smoke validation.

## Release smoke checklist

Run these before tag/publish in the final release job:

```text
python -m pytest tests/ -q
git diff --check
python scripts/public_readiness.py --check
python -m build
bash examples/full_demo.sh
bash examples/rollback_selective_demo.sh
```

## E2E/review gates

- Confirm `python scripts/public_readiness.py --check` prints `release-tag=not-created` during release-prep.
- Confirm full demo artifacts show: watch-run, timeline, verify-artifacts, review, rollback plan/apply, operator packet, and manual-review/compensation language for external effects.
- Confirm README install commands are only treated as post-tag instructions; do not validate them against an uncreated tag in this PR.
- Review `docs/v0.2-readiness-matrix.md` and `docs/completion-gap-audit.md` for overclaims before publishing release notes externally.
- Verify there is no PyPI upload, GitHub release creation, or git tag in this branch.

## Verification snapshot

To be filled by the release-prep PR after local verification. Expected commands:

```text
python -m pytest tests/ -q
python scripts/public_readiness.py --check
python -m build
```
