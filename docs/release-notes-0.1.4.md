# SafeLoop v0.1.4 — public alpha

SafeLoop v0.1.4 is a rollback-centric local safety runtime for long-running agent tasks.

## Highlights

- Reviewed rollback plans and apply paths for whole-run, checkpoint, selected-file, selected-hunk, and action-level local changes.
- Compensation/manual-review planning for external side effects without claiming exact rollback.
- Policy-driven rollback suggestions for local and external findings, with no auto-apply.
- Public readiness checks and an end-to-end selective rollback demo.
- Tamper-evident local artifacts for review; not a remote transparency log or tamper-proof audit system.

## Boundaries

- Exact rollback applies only to covered local file changes that SafeLoop can verify at apply time.
- External side effects are manual-review, compensation, best-effort, or verified states; they are never exact rollback.
- Every apply path remains plan-gated.
- No production external adapters, hosted approval workflow, or remote transparency log are included in this release.

## Verification

```text
pytest -q
310 passed

git diff --check
OK

python scripts/public_readiness.py --check
public-readiness: ok

python -m build
OK

bash examples/rollback_selective_demo.sh
rollback selective demo: ok
```
