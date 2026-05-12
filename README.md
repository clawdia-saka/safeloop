# SafeLoop

**SafeLoop 0.1.4 is the current public release.**

SafeLoop is a non-blocking watchdog and reversible local timeline for long-running AI agents.

It records local file side effects, checkpoints repo changes, verifies tamper-evident artifacts, supports exact local undo for covered file changes, and marks external side effects as **not tracked** in this release instead of pretending they are blindly reversible.

In short: **roll back covered local files, compensate or manually review external effects, and audit everything.** See [`docs/recoverability-first.md`](docs/recoverability-first.md) for the product boundary.

## Problem

Agents can run for minutes or hours and leave operators asking:

- what command actually ran?
- what files changed, and when?
- are the artifacts intact or tampered with?
- can a checkpoint be locally undone?
- did anything external happen that needs manual compensation?

SafeLoop 0.1.4 hardens the local watchdog, delta-audit packet, and control-plane evidence workflow for those questions.

## Quickstart

One-line install from the public release tag:

```bash
pipx install git+https://github.com/clawdia-saka/safeloop.git@v0.1.4
```

If you do not use `pipx`, install into a local virtual environment instead:

```bash
python3 -m venv .venv && . .venv/bin/activate && python -m pip install 'safeloop @ git+https://github.com/clawdia-saka/safeloop.git@v0.1.4'
```

Minimal end-to-end local rollback smoke test:

```bash
rm -rf /tmp/safeloop-demo-repo /tmp/safeloop-demo-runs
mkdir -p /tmp/safeloop-demo-repo
cd /tmp/safeloop-demo-repo
git init -q
git config user.email demo@example.test
git config user.name demo
echo base > note.txt
git add note.txt && git commit -q -m init

safeloop watch-run --task-id demo --repo "$PWD" --run-root /tmp/safeloop-demo-runs -- \
  python -c "from pathlib import Path; Path('note.txt').write_text('changed by safeloop\\n')"

RUN_DIR="$(find /tmp/safeloop-demo-runs -maxdepth 1 -type d -name 'run-*' | head -1)"
RUN_ID="$(basename "$RUN_DIR")"
safeloop review "$RUN_DIR"
safeloop explain "$RUN_DIR"
safeloop rollback plan "$RUN_DIR" "$RUN_ID" --files note.txt
safeloop rollback apply "$RUN_DIR" "$RUN_ID" --files note.txt
cat note.txt  # base
```

External side effects are manual-review/compensation only; SafeLoop never claims exact rollback for external systems.

## Demo commands

The public demo path is intentionally five commands:

```bash
safeloop watch-run
safeloop timeline
safeloop verify-artifacts
safeloop rollback plan
safeloop rollback apply
```

Install for local development from a checkout:

```bash
python -m pip install -e .
```

Run an agent command under the watchdog with the current 0.1.4 flow:

```bash
safeloop watch-run \
  --task-id demo \
  --repo /path/to/repo \
  -- python agent.py
```

The command prints the run directory, normally under `~/.safeloop/runs/<run-id>/` unless `--run-root` is provided.

Inspect the timeline, verify the tamper-evident artifact packet, then create an operator-readable rollback plan:

```bash
safeloop timeline RUN_DIR
safeloop timeline RUN_DIR --json
safeloop timeline RUN_DIR --checkpoint cp-0001
safeloop verify-artifacts RUN_DIR
safeloop rollback plan RUN_DIR RUN_ID cp-0001
```

Apply covered local-file rollback only after reviewing `rollback-plan.json`:

```bash
safeloop rollback apply RUN_DIR RUN_ID cp-0001
```

Compatibility aliases remain available for older scripts:

- `safeloop watch --loop ...` is an alias-compatible form of `safeloop watch-run ...`.
- `safeloop undo RUN_DIR RUN_ID cp-0001 --dry-run|--apply` remains available for the older dry-run/apply UX.

Or run the checked-in demo script for the release packet flow (`watch-run` → `timeline` → `verify-artifacts` → `rollback plan/apply`):

```bash
bash examples/watchdog_demo.sh
```

For the external side-effect boundary, run:

```bash
bash examples/recoverability_external_effect_demo.sh
```

That demo rolls back a covered local file while leaving fake external API evidence marked `external_review_required`. A static one-page visual is available at [`examples/recoverability_demo.html`](examples/recoverability_demo.html).

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
- Existing runtime boundary demos, including repeated resume, remain documented as local lifecycle examples alongside the watchdog release.
- Framework integrations (LangGraph, CrewAI, AutoGen, browser agents, hosted adapters) are future surfaces; the current public path is the local CLI flow above.

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

For the public MVP readiness boundary, see [`docs/public-mvp-readiness.md`](docs/public-mvp-readiness.md).

## Rollback public readiness skeleton

The public readiness skeleton for SafeLoop 0.1.4 demonstrates the local rollback workflow end to end:
watch a long-running local task, review and explain rollback groups, plan/apply rollback to start,
plan/apply selected files, plan/apply selected hunks, and run `policy-check`. The scripted demo is
`examples/rollback_selective_demo.sh`.

Boundary language for public docs: exact rollback is only claimed for covered local file changes.
External side effects require compensation or manual review and are not exact rollback. Local artifacts
are tamper-evident review aids, not tamper-proof guarantees. SafeLoop does not claim a remote
transparency log unless one is explicitly implemented and configured.

## Historical references

These documents describe earlier release boundaries and remain useful for context, but SafeLoop 0.1.4 is the current public release described above:

- [`docs/safeloop-0.0.3-agent-watchdog-rc.md`](docs/safeloop-0.0.3-agent-watchdog-rc.md)
- [`docs/release-notes-0.0.3.md`](docs/release-notes-0.0.3.md)
- [`docs/control-plane.md`](docs/control-plane.md)
- [`docs/approval-lifecycle.md`](docs/approval-lifecycle.md)
- [`docs/control-plane-threat-model.md`](docs/control-plane-threat-model.md)
- [`docs/release-notes-0.1.0.md`](docs/release-notes-0.1.0.md)
- [`examples/control-plane-local-demo/`](examples/control-plane-local-demo/)
