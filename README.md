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

SafeLoop 0.1.4 hardens the local watchdog, delta-audit packet, and control-plane evidence workflow for those questions.

## Demo commands

Install for local development:

```bash
python -m pip install -e .
```

Run an agent under the watchdog:

```bash
python -m safeloop.cli watch --loop \
  --task-id demo \
  --repo /path/to/repo \
  -- python agent.py

# `watch-run` remains available as a compatibility alias.
```

The command prints the run directory, normally under `~/.safeloop/runs/<run-id>/` unless `--run-root` is provided.

Inspect the timeline, verify the tamper-evident artifact packet, then create an operator-readable rollback plan:

```bash
python -m safeloop.cli timeline RUN_DIR
python -m safeloop.cli timeline RUN_DIR --json
python -m safeloop.cli timeline RUN_DIR --checkpoint cp-0001
python -m safeloop.cli verify-artifacts RUN_DIR
python -m safeloop.cli rollback plan RUN_DIR RUN_ID cp-0001
```

Apply covered local-file rollback only after reviewing `rollback-plan.json`:

```bash
python -m safeloop.cli rollback apply RUN_DIR RUN_ID cp-0001

# `undo RUN_DIR RUN_ID cp-0001 --dry-run|--apply` remains available as a compatibility alias.
```

Or run the checked-in demo script for the release packet flow (`watch-run` → `timeline` → `verify-artifacts` → `undo --dry-run`):

```bash
bash examples/watchdog_demo.sh
```

## What it does today

SafeLoop 0.1.4 writes local watchdog and review-aid artifacts such as:

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

- `watch --loop` (`watch-run` compatibility alias): executes a command in a repo, captures stdout/stderr, records process result, creates run-local monotonic checkpoints for changed repo state, and binds artifacts into both checkpoint metadata and the timeline hash chain.
- Artifact contract: `run.json`, `timeline.jsonl`, checkpoint metadata, `restore-manifest.json` with `schema_version: restore-manifest.v2`, complete command capture files, and an always-present `side-effects.jsonl` placeholder.
- Checkpoint parent binding: each checkpoint records parent, sequence, allocation mode, previous state digest, current state digest, and artifact digests.
- `verify-artifacts`: checks timeline hash chain, bound artifact digests, checkpoint parent chain, process-result digest, command capture completeness, invalid partial finalization, and run final hash; writes `verification/verify-artifacts-result.json`.
- `timeline`: prints run status, command metadata, checkpoint table, undo status, side-effect status, and latest hash.
- `rollback plan` / `rollback apply`: baseline operator UX for covered local file changes. `rollback plan` writes deterministic `rollback-plan.json` (`schema_version: rollback-plan.v1`) and `rollback apply` writes checkpoint-local `rollback-result.json` (`schema_version: rollback-result.v1`) with post-apply `verify-artifacts` status. External side effects are classified for manual review/compensation, never exact rollback.
- `undo`: compatibility alias for the older dry-run/apply UX and artifact paths (`undo-preflight.json`, `rollback-plan.json`, and `undo-result.json`).
- Existing runtime boundary demos, including repeated resume, remain documented as local lifecycle examples alongside the watchdog RC.

## Compensation is not rollback

SafeLoop separates exact local undo from external compensation.

| Term | Means | Does not mean |
| --- | --- | --- |
| Local rollback | SafeLoop applies a reviewed rollback plan for covered local file changes. | External systems, hidden state, or every possible side effect were reset. |
| Compensation | SafeLoop ran a configured cleanup hook after execution began. | Exact rollback, time travel, or an “as-if-never-happened” state. |
| `compensation_failed` | SafeLoop tried cleanup and the hook failed. | The original side effect was safely undone or can be ignored. |

A `compensated` run means the configured compensation hook completed; it does not mean exact rollback or an “as-if-never-happened” state. A `compensation_failed` run means cleanup is incomplete or uncertain and needs operator review. See [`docs/faq.md`](docs/faq.md), [`docs/specs/state-machine-and-journal-schema.md`](docs/specs/state-machine-and-journal-schema.md), and [`docs/case-studies/boundary-scenarios.md`](docs/case-studies/boundary-scenarios.md) for the boundary examples.

## What it does not claim

SafeLoop is intentionally narrow in this release:

- not tamper-proof; artifacts are **tamper-evident local artifacts**
- not a hosted control plane yet
- external actions are not blindly reversible
- gitignored files are out of scope unless configured
- no hosted HTTP dashboard v2 or SaaS control plane
- no Slack/GitHub/Vercel external adapter authority
- no remote transparency log or full rollback-to semantics yet

## Development

Canonical runtime/API contracts:

- State machine and journal schema: `docs/specs/state-machine-and-journal-schema.md`

Run tests:

```bash
pytest -q
```

Build a wheel:

```bash
python -m build --wheel
```

For the public MVP readiness boundary, see [`docs/public-mvp-readiness.md`](docs/public-mvp-readiness.md). Historical watchdog contract notes remain in [`docs/safeloop-0.0.3-agent-watchdog-rc.md`](docs/safeloop-0.0.3-agent-watchdog-rc.md) and [`docs/release-notes-0.0.3.md`](docs/release-notes-0.0.3.md).

For the 0.1.0 local control-plane MVP, see [`docs/control-plane.md`](docs/control-plane.md), [`docs/approval-lifecycle.md`](docs/approval-lifecycle.md), [`docs/control-plane-threat-model.md`](docs/control-plane-threat-model.md), and [`docs/release-notes-0.1.0.md`](docs/release-notes-0.1.0.md). A local demo is in [`examples/control-plane-local-demo/`](examples/control-plane-local-demo/).

## Rollback public readiness skeleton

The public readiness skeleton for SafeLoop 0.1.4 demonstrates the local rollback workflow end to end:
watch a long-running local task, review and explain rollback groups, plan/apply rollback to start,
plan/apply selected files, plan/apply selected hunks, and run `policy-check`. The scripted demo is
`examples/rollback_selective_demo.sh`.

Boundary language for public docs: exact rollback is only claimed for covered local file changes.
External side effects require compensation or manual review and are not exact rollback. Local artifacts
are tamper-evident review aids, not tamper-proof guarantees. SafeLoop does not claim a remote
transparency log unless one is explicitly implemented and configured.
