# SafeLoop 0.0.3 — Agent Watchdog RC

## Positioning

SafeLoop is a non-blocking watchdog and reversible timeline for long-running AI agents.
It records local side effects, checkpoints repo changes, verifies artifact integrity, supports exact local undo, and hands off irreversible external actions as compensation plans.

## Release line

- `0.0.2`: local watchdog undo MVP.
- `0.0.3`: agent process watchdog product RC.

## Scope guard

### Do now

1. Artifact contract freeze
2. `safeloop watch-run`
3. `safeloop verify-artifacts`
4. `safeloop timeline`
5. Undo UX polish
6. README / demo packet update
7. Checkpoint parent binding
8. `side-effects.jsonl` placeholder
9. Nightloop observation update

### Do not do yet

- HTTP dashboard v2
- External adapters: Slack/GitHub/Vercel/etc.
- HMAC/Ed25519 signing
- Remote transparency log
- Full `rollback-to` implementation
- Branch/fork timeline semantics
- Hosted control plane
- RBAC

## PR order

### PR 0 — Artifact contract freeze

Thin implementation; freeze schemas and artifact layout before `watch-run` expands the surface.

Tasks:

- Define `run.json` schema.
- Define `timeline.jsonl` event schema.
- Define checkpoint directory layout.
- Preserve filename `restore-manifest.json`, with `schema_version: restore-manifest.v2`.
- Generate `side-effects.jsonl`, even if empty.
- Document artifact digest binding targets.
- Add checkpoint parent binding.

### PR 1 — `safeloop watch-run`

CLI:

```bash
safeloop watch-run \
  --task-id demo \
  --repo /repo \
  --debounce-ms 750 \
  --max-interval-sec 0 \
  -- python agent.py
```

Required artifacts:

```text
RUN_DIR/
  run.json
  timeline.jsonl
  command.stdout.txt
  command.stderr.txt
  process-result.json
  side-effects.jsonl
  checkpoints/
    cp-0001/
      checkpoint.json
      manifest.json
      diff.patch
      restore-manifest.json
      summary.md
```

Acceptance:

- Command execution and checkpoint timeline are bound into the same run.
- Multiple repo changes create monotonic checkpoints.
- Duplicate states are skipped.
- stdout/stderr are drained concurrently and persisted.
- Command failure preserves artifacts and marks run `failed`.
- Partial/corrupt run evidence is preserved and marked `invalid_partial`.
- `.git/`, `.safeloop/`, run artifacts, and gitignored out-of-scope files are excluded.
- Default run dir should be outside repo when practical, e.g. `~/.safeloop/runs/<run-id>/`.

### PR 2 — `safeloop verify-artifacts`

CLI:

```bash
safeloop verify-artifacts RUN_DIR
```

Verify:

- timeline hash chain
- latest event hash
- checkpoint parent chain
- manifest digest
- diff digest
- restore-manifest digest
- summary digest
- undo-result digest if present
- process-result digest
- run final hash
- missing / modified / tampered artifacts

Result classes:

- `valid`
- `warning`
- `invalid`

Write JSON result:

```text
verification/verify-artifacts-result.json
```

Operator copy must say `tamper-evident local artifacts`, not `tamper-proof`.

### PR 3 — `safeloop timeline`

CLI:

```bash
safeloop timeline RUN_DIR
safeloop timeline RUN_DIR --json
safeloop timeline RUN_DIR --checkpoint cp-0002
```

Show:

- run id / task id / command / status / exit code / checkpoint count / latest hash
- checkpoint table: id, parent, digest status, undo status, blocked reason, side-effect status, timestamp
- latest important events

This is the dashboard-v2 precursor; do not build HTTP dashboard in 0.0.3.

### PR 4 — Undo UX polish

Standardize operator-readable output for:

- dry-run pass
- blocked undo
- successful undo

Must include:

- run id
- checkpoint id
- mode
- file counts
- external side-effect status
- exact blocker reason when blocked
- artifact paths: `undo-preflight.json`, `rollback-plan.json`, `undo-result.json`

Expose undo-result status to timeline/dashboard surfaces where already present.

### PR 5 — README / demo packet

Replace approval-gate-centric story with watchdog/reversible timeline story.

README structure:

1. One-liner
2. Problem
3. Demo commands
4. What it does today
5. What it does not claim

Explicit non-claims:

- not tamper-proof
- not hosted control plane yet
- external actions are not blindly reversible
- gitignored files are out of scope unless configured

## Artifact layout

```text
runs/
  run-20260502-100112-demo/
    run.json
    timeline.jsonl
    command.stdout.txt
    command.stderr.txt
    process-result.json
    side-effects.jsonl
    compensation-plan.json
    compensation-plan.md
    rollback-plan.json
    daily-operator-packet.md

    checkpoints/
      cp-0001/
        checkpoint.json
        manifest.json
        diff.patch
        restore-manifest.json
        summary.md
        undo-preflight.json
        undo-result.json

    verification/
      verify-artifacts-result.json
```

Rules:

- Write artifact first, then append the timeline event that binds it.
- Write artifacts via temp file plus atomic rename.
- Bind artifact digests in timeline events.
- Exclude watcher artifact directories from repo watch scope.
- Prefer default run dirs outside repo; if inside repo, use `.safeloop/` and exclude it.

## `run.json` schema

```json
{
  "schema_version": "run.v1",
  "run_id": "run-20260502-100112-demo",
  "task_id": "demo",
  "agent_id": null,
  "repo": "/repo",
  "command": ["python", "agent.py"],
  "cwd": "/repo",
  "started_at": "2026-05-02T10:01:12+09:00",
  "ended_at": "2026-05-02T10:01:33+09:00",
  "status": "completed",
  "exit_code": 0,
  "checkpoint_count": 3,
  "external_side_effect_count": 0,
  "latest_event_hash": "sha256:...",
  "final_event_hash": "sha256:..."
}
```

Statuses:

- `running`
- `completed`
- `failed`
- `watchdog_stopped`
- `invalid_partial`

## `timeline.jsonl` event schema

```json
{
  "schema_version": "timeline-event.v1",
  "event_id": "evt-0007",
  "seq": 7,
  "type": "checkpoint_created",
  "timestamp": "2026-05-02T10:01:18+09:00",
  "prev_event_hash": "sha256:...",
  "payload": {
    "checkpoint_id": "cp-0002",
    "parent_checkpoint_id": "cp-0001",
    "artifact_digests": {
      "checkpoint.json": "sha256:...",
      "manifest.json": "sha256:...",
      "diff.patch": "sha256:...",
      "restore-manifest.json": "sha256:...",
      "summary.md": "sha256:..."
    }
  },
  "event_hash": "sha256:..."
}
```

Minimum event types:

- `run_started`
- `checkpoint_created`
- `artifact_bound`
- `external_side_effect_recorded`
- `compensation_handoff_created`
- `undo_preflight_completed`
- `undo_completed`
- `process_exited`
- `watchdog_stopped`
- `run_closed`
- `verification_completed`

## Checkpoint parent binding

Each checkpoint must include:

```json
{
  "checkpoint_id": "cp-0003",
  "parent_checkpoint_id": "cp-0002",
  "previous_state_digest": "sha256:...",
  "current_state_digest": "sha256:..."
}
```

`rollback-to` and branch/fork semantics are deferred, but history must not be overwritten.

## Side-effect registry placeholder

Generate `side-effects.jsonl` even when empty.

Future record shape:

```json
{
  "schema_version": "side-effect.v1",
  "effect_id": "fx-0001",
  "checkpoint_id": "cp-0003",
  "type": "github_comment",
  "target": "repo/issue#123",
  "payload_digest": "sha256:...",
  "status": "sent",
  "reversibility": "compensation_required",
  "compensation_strategy": "manual_handoff",
  "compensation_artifact": "compensation-plan.md"
}
```

No real external adapters in 0.0.3.

## Nightloop observation update

Track deltas on:

- open runs
- completed runs
- failed runs
- invalid partial runs
- checkpoint count delta
- latest event hash delta
- verify-artifacts result
- undoable checkpoint count
- blocked undo count
- compensation required count
- external side-effect count
- tamper invalid count

Morning briefs stay delta-only.

## Test plan

### Watch-run

- `test_watch_run_creates_run_dir`
- `test_watch_run_captures_stdout_stderr`
- `test_watch_run_records_process_result_success`
- `test_watch_run_records_process_result_failure`
- `test_watch_run_creates_multiple_checkpoints`
- `test_watch_run_does_not_duplicate_identical_state`
- `test_watch_run_excludes_safeloop_artifacts`
- `test_watch_run_records_monotonic_checkpoint_ids`
- `test_watch_run_binds_checkpoint_parent`
- `test_watch_run_finalizes_run_json`

### Verify artifacts

- `test_verify_artifacts_valid_run`
- `test_verify_artifacts_detects_timeline_hash_tamper`
- `test_verify_artifacts_detects_manifest_digest_mismatch`
- `test_verify_artifacts_detects_diff_digest_mismatch`
- `test_verify_artifacts_detects_missing_bound_artifact`
- `test_verify_artifacts_detects_invalid_parent_chain`
- `test_verify_artifacts_allows_optional_missing_artifact_as_warning`

### Undo UX

- `test_undo_dry_run_prints_operator_summary`
- `test_undo_blocked_prints_reason`
- `test_undo_success_prints_undo_result_path`
- `test_dashboard_shows_undo_result_status`

### Demo

- `test_demo_script_watch_diff_undo_verify`

## Exit criteria

```text
pytest -q pass
wheel build pass
```

Demo script passes:

1. `safeloop watch-run --task-id demo --repo repo -- python agent.py`
2. `safeloop timeline RUN_DIR`
3. `safeloop undo RUN_DIR RUN_ID cp-0002 --dry-run`
4. `safeloop undo RUN_DIR RUN_ID cp-0002 --apply`
5. `safeloop verify-artifacts RUN_DIR`

Artifacts:

- `run.json` exists
- `timeline.jsonl` valid
- `command.stdout.txt` exists
- `command.stderr.txt` exists
- `process-result.json` exists
- checkpoints are monotonic
- checkpoint parent binding exists
- `restore-manifest.json` exists
- `verify-artifacts` returns valid after normal run
- `verify-artifacts` returns invalid after tampering

README:

- matches actual CLI
- does not overclaim external reversibility
- uses watchdog/reversible timeline positioning

## 0.0.4 / 0.1.0

- `0.0.4`: multi-checkpoint rollback semantics, `rollback-to`, post-rollback event.
- `0.0.5`: external side-effect registry, compensation contracts, outbox model, fake/demo adapters.
- `0.1.0`: real-agent integration demo: Claude/Codex/Hermes wrapper, Git worktree integration, CI smoke, dashboard v2.

0.1.0 demo line:

> An agent wrote code overnight. SafeLoop shows every checkpoint, verifies the timeline, explains what can be undone, and produces compensation handoffs for anything that cannot be blindly reversed.
