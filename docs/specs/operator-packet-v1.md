# SafeLoop operator packet v1

Operator packet v1 is a narrow, operator-readable handoff artifact for SafeLoop demos and local watchdog runs. It is intentionally a Markdown contract, not a scheduler, approval service, control plane, or external rollback mechanism.

A v1 packet MUST include these sections:

- `## Operator packet v1 metadata`
- `## Summary`
- `## Action and evidence`
- `## Compensation and recovery options`
- `## Manual review decision`
- `## Non-goals and boundary`

Required metadata fields:

- `Schema: safeloop.operator-packet.v1`
- `Run directory:`
- `Run ID:`
- `Checkpoint ID:` when the packet includes local rollback material
- `Scope:` describing the local boundary

Required boundary language:

- Exact rollback claims are limited to covered local repository file changes captured by SafeLoop.
- Actions outside the local repository require manual review or separate compensation.
- The packet must not imply that external systems, schedulers, control planes, production databases, messages, or hosted services were rolled back by local file recovery.

The Gbrain context demo writes `operator-packet.md` using this structure while preserving the existing role split: Gbrain supplies retrieved context evidence only; the agent decides and acts; SafeLoop records action evidence, a local rollback plan, and manual-review material.
