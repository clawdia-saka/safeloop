# SafeLoop 0.0.3 Release Notes

SafeLoop 0.0.3 is the agent watchdog release candidate. It keeps the project private-first and local-first: runs create an operator-readable artifact packet on disk, then expose CLI surfaces to inspect, verify, and dry-run local undo.

## What changed since 0.0.1 / 0.0.2

- `0.0.1`: established the local runtime/journal foundations and boundary demos for reversible local lifecycle work.
- `0.0.2`: added the local watchdog undo MVP for covered repo file changes.
- `0.0.3`: turns that MVP into an agent process watchdog RC:
  - `watch-run` executes a command, captures stdout/stderr, records process result, and creates monotonic checkpoints.
  - `timeline` presents run status, command metadata, checkpoint state, undo status, side-effect status, and latest hash.
  - `verify-artifacts` checks the timeline hash chain and bound artifact digests, then writes `verification/verify-artifacts-result.json`.
  - `undo --dry-run` / `undo --apply` provide operator-readable local file undo UX for covered changes.
  - Checkpoints include parent binding and state digests.
  - `side-effects.jsonl` is always present as a placeholder; external side effects are tracked/not auto-reversed in this RC.

## Demo flow

Use the checked-in demo script to exercise the release packet flow:

```bash
bash examples/watchdog_demo.sh
```

The script runs:

1. `python -m safeloop.cli watch-run --task-id demo --repo "$DEMO_REPO" --run-root "$RUN_ROOT" -- python "$DEMO_REPO/agent.py"`
2. `python -m safeloop.cli timeline "$RUN_DIR"`
3. `python -m safeloop.cli verify-artifacts "$RUN_DIR"`
4. `python -m safeloop.cli undo "$RUN_DIR" "$RUN_ID" "$CHECKPOINT_ID" --dry-run`

## Non-claims / scope guard

- SafeLoop artifacts are tamper-evident local artifacts, not tamper-proof records.
- SafeLoop 0.0.3 is not a hosted control plane.
- External side effects are tracked/not auto-reversed; operators must handle manual compensation where needed.
- Gitignored files are out of scope unless configured.
- No HTTP dashboard v2, Slack/GitHub/Vercel adapters, signing, remote transparency log, RBAC, or full rollback-to semantics are included in 0.0.3.

## Deferred items

- Multi-checkpoint rollback semantics and `rollback-to`.
- External side-effect registry, compensation contracts, outbox model, and demo/fake adapters.
- Real-agent integration wrappers and CI smoke around Claude/Codex/Hermes-style workflows.
- Dashboard v2 and hosted/team-control surfaces.
- Signing, remote transparency log, and stronger supply-chain integrity guarantees.
