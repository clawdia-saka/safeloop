# Recoverability-first SafeLoop

SafeLoop is intentionally a **recoverability-first** runtime, not a promise that every agent action can be reversed.

The product boundary is:

1. **Covered local file changes** can be planned, reviewed, and rolled back when SafeLoop can verify the covered state at apply time. These are the covered local file changes SafeLoop can reason about.
2. **Actions outside the local repo** are never treated as exact rollback. They require manual review or compensation, and the artifacts must say so.
3. **Audit artifacts** should answer what ran, what changed, what was verified, and which risks remain.

Older artifacts may call actions outside the local repo **External side effects**; the operator-facing meaning is the same boundary, not exact rollback.

## Operator vocabulary

- **Rollback** means restoring covered local repo files from verified SafeLoop artifacts. It does not mean undoing emails, API calls, tickets, browser actions, database writes, or other actions outside the local repo.
- **Compensation** means recording a cleanup or correction path for an action outside the local repo, such as closing an issue or sending a correction message. Compensation remains `exact_rollback: false` because the outside action already happened.
- **Manual handoff** means SafeLoop has surfaced the evidence and an operator must decide or complete the next step. Use this when SafeLoop cannot safely verify or perform the recovery action on its own.
- **Action groups** are operator review bundles: related files, hunks, checkpoints, and optional action IDs grouped so a person can review one unit of work before choosing rollback, compensation, or handoff.

## Public UX to keep first-class

The public story should start with this five-command path:

```bash
safeloop watch-run
safeloop timeline
safeloop verify-artifacts
safeloop rollback plan
safeloop rollback apply
```

Other commands remain useful, but should be framed carefully:

- `watch --loop` is a compatibility alias for older scripts.
- `undo` is a compatibility alias for the older dry-run/apply UX.
- `review`, `explain`, `policy-check`, and HTML/reporting commands are advanced review aids, not the first thing new users need.

## Full public demo packet

The checked-in full demo is `examples/full_demo.sh`. It is intentionally a narrow local packet, not a UI redesign or hosted control-plane claim:

1. Creates a temporary git repo and records the baseline file state.
2. Runs `safeloop watch-run` around an agent command that changes a covered local file and writes a fake outside-service log.
3. Runs `safeloop timeline`, `safeloop verify-artifacts`, `safeloop review`, and `safeloop rollback plan` to produce local change evidence and an operator-readable packet.
4. Applies `safeloop rollback apply` only for the covered local file.
5. Runs `python scripts/public_readiness.py --check` as the demo verification gate.

The operator packet points to the checkpoint diff, verification result, rollback plan, and fake external evidence. It must keep the boundary explicit: local file rollback can be exact when preconditions hold; the outside-service log is compensation/manual-handoff evidence and is not rolled back by SafeLoop.

## Why this framing matters

A broad claim such as “AI agent actions can be rolled back” is unsafe. It hides the difference between local, observed, covered state and external systems that may already have delivered messages, updated databases, or triggered downstream work.

SafeLoop should instead make the boundary explicit:

> Roll back covered local files. Compensate or manually review actions outside the local repo. Audit everything.

That is the product value: fewer surprises after long-running agents, not magical time travel.

## Framework integrations

LangGraph, CrewAI, AutoGen, browser agents, and hosted adapters are future integration surfaces. They should not lead the README until the local CLI path is unmistakably clear and the external side-effect boundary remains visible in examples.
