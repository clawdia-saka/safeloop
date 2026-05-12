# Recoverability-first SafeLoop

SafeLoop is intentionally a **recoverability-first** runtime, not a promise that every agent action can be reversed.

The product boundary is:

1. **Covered local file changes** can be planned, reviewed, and rolled back when SafeLoop can verify the covered state at apply time. These are the covered local file changes SafeLoop can reason about.
2. **External side effects** are never treated as exact rollback. They require manual review or compensation, and the artifacts must say so.
3. **Audit artifacts** should answer what ran, what changed, what was verified, and which risks remain.

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

## Why this framing matters

A broad claim such as “AI agent actions can be rolled back” is unsafe. It hides the difference between local, observed, covered state and external systems that may already have delivered messages, updated databases, or triggered downstream work.

SafeLoop should instead make the boundary explicit:

> Roll back covered local files. Compensate or manually review external effects. Audit everything.

That is the product value: fewer surprises after long-running agents, not magical time travel.

## Framework integrations

LangGraph, CrewAI, AutoGen, browser agents, and hosted adapters are future integration surfaces. They should not lead the README until the local CLI path is unmistakably clear and the external side-effect boundary remains visible in examples.
