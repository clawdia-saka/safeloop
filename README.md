# SafeLoop

SafeLoop is a non-blocking watchdog and reversible local timeline for long-running AI agents.

It records local file side effects, checkpoints repo changes, verifies tamper-evident artifacts, supports exact local undo for covered file changes, and marks external side effects as **not tracked** in this RC instead of pretending they are blindly reversible.

## Problem

Agents can run for minutes or hours and leave operators asking:

- what command actually ran?
- what files changed, and when?
- are the artifacts intact or tampered with?
- can a checkpoint be locally undone?
- did anything external happen that needs manual compensation?

SafeLoop 0.0.3 focuses on a local watchdog/reversible-timeline workflow for those questions.

## Demo commands

Install for local development:

```bash
python -m pip install -e .
```

Run an agent under the watchdog:

```bash
python -m safeloop.cli watch-run \
  --task-id demo \
  --repo /path/to/repo \
  -- python agent.py
```

The command prints the run directory, normally under `~/.safeloop/runs/<run-id>/` unless `--run-root` is provided.

Inspect the timeline, verify the tamper-evident artifact packet, then dry-run local undo:

```bash
python -m safeloop.cli timeline RUN_DIR
python -m safeloop.cli timeline RUN_DIR --json
python -m safeloop.cli timeline RUN_DIR --checkpoint cp-0001
python -m safeloop.cli verify-artifacts RUN_DIR
python -m safeloop.cli undo RUN_DIR RUN_ID cp-0001 --dry-run
```

Apply local undo only after reviewing the dry-run output:

```bash
python -m safeloop.cli undo RUN_DIR RUN_ID cp-0001 --apply
```

Or run the checked-in demo script for the release packet flow (`watch-run` → `timeline` → `verify-artifacts` → `undo --dry-run`):

```bash
bash examples/watchdog_demo.sh
```

## What it does today

SafeLoop 0.0.3 RC writes a local run artifact packet:

```text
RUN_DIR/
  run.json
  timeline.jsonl
  command.stdout.txt
  command.stderr.txt
  process-result.json
  side-effects.jsonl
  rollback-plan.json
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

Implemented surfaces:

- `watch-run`: executes a command in a repo, captures stdout/stderr, records process result, creates monotonic checkpoints for changed repo state, and binds artifacts into a timeline hash chain.
- Artifact contract: `run.json`, `timeline.jsonl`, checkpoint metadata, `restore-manifest.json` with `schema_version: restore-manifest.v2`, and an always-present `side-effects.jsonl` placeholder.
- Checkpoint parent binding: each checkpoint records parent, previous state digest, and current state digest.
- `verify-artifacts`: checks timeline hash chain, bound artifact digests, checkpoint parent chain, process-result digest, and run final hash; writes `verification/verify-artifacts-result.json`.
- `timeline`: prints run status, command metadata, checkpoint table, undo status, side-effect status, and latest hash.
- `undo`: demo-ready dry-run/apply UX for covered local file creations, including operator-readable artifact paths for `undo-preflight.json`, `rollback-plan.json`, and `undo-result.json`.
- Existing runtime boundary demos, including repeated resume, remain documented as local lifecycle examples alongside the watchdog RC.

## What it does not claim

SafeLoop is intentionally narrow in this release:

- not tamper-proof; artifacts are **tamper-evident local artifacts**
- not a hosted control plane yet
- external actions are not blindly reversible
- gitignored files are out of scope unless configured
- no HTTP dashboard v2 in 0.0.3
- no Slack/GitHub/Vercel external adapters in 0.0.3
- no signing, remote transparency log, RBAC, or full rollback-to semantics yet

## Development

Run tests:

```bash
pytest -q
```

Build a wheel:

```bash
python -m build --wheel
```

For the 0.0.3 watchdog contract, see [`docs/safeloop-0.0.3-agent-watchdog-rc.md`](docs/safeloop-0.0.3-agent-watchdog-rc.md). For release notes and deltas from 0.0.1/0.0.2, see [`docs/release-notes-0.0.3.md`](docs/release-notes-0.0.3.md).
