# SafeLoop rollback

SafeLoop rollback support is a local-file rollback and review workflow. The public demo in
`examples/rollback_selective_demo.sh` exercises:

1. `safeloop watch --loop` / `watch-run` for a long-running task.
2. `safeloop review` and `safeloop explain` for operator review. `explain` uses plain language for rollback, compensation, manual handoff, and action groups.
3. `safeloop rollback plan/apply --to-start`.
4. `safeloop rollback plan/apply --files ...` for selected local files.
5. `safeloop rollback plan/apply --hunks ...` for selected hunks.
6. `safeloop policy-check` for do-not-do policy findings and suggested rollback commands.

## Boundaries

Exact rollback is only covered for local files that SafeLoop captured and can verify at apply time.
Actions outside the local repo are not exact rollback; they require compensation or manual handoff. Local
artifacts are tamper-evident, not tamper-proof. There is no remote transparency log in the public MVP
unless a deployment explicitly implements one.

## Operator terms

- **Rollback:** restore covered local repo files from verified artifacts.
- **Compensation:** record a cleanup or correction plan for actions outside the local repo; it is not proof the outside system is back to an as-if-never-happened state.
- **Manual handoff:** route evidence to an operator when SafeLoop cannot safely verify or complete the recovery automatically.
- **Action groups:** related files, hunks, checkpoints, and optional action IDs shown together so an operator can choose what to roll back or hand off.

Action rollback and compensation/manual-review demo steps are intentionally documented as conditional
hooks until their prerequisite PRs land and this branch is rebased.
