# SafeLoop rollback

SafeLoop rollback support is a local-file rollback and review workflow. The public demo in
`examples/rollback_selective_demo.sh` exercises:

1. `safeloop watch --loop` / `watch-run` for a long-running task.
2. `safeloop review` and `safeloop explain` for operator review.
3. `safeloop rollback plan/apply --to-start`.
4. `safeloop rollback plan/apply --files ...` for selected local files.
5. `safeloop rollback plan/apply --hunks ...` for selected hunks.
6. `safeloop policy-check` for do-not-do policy findings and suggested rollback commands.

## Boundaries

Exact rollback is only covered for local files that SafeLoop captured and can verify at apply time.
External side effects are not exact rollback; they require compensation or manual review. Local
artifacts are tamper-evident, not tamper-proof. There is no remote transparency log in the public MVP
unless a deployment explicitly implements one.

Action rollback and compensation/manual-review demo steps are intentionally documented as conditional
hooks until their prerequisite PRs land and this branch is rebased.
