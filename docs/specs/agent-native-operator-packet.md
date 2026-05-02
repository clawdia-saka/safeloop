# Agent-native operator packet specification

Status: roadmap/spec draft for SafeLoop 0.2.0.

## Purpose

The agent-native operator packet is SafeLoop's default review surface for risky actions. It is a local artifact bundle that can be handed to a human or a review agent such as OpenClaw, Hermes, Codex, or Claude Code.

The review agent returns a structured verdict. SafeLoop, not the review agent, imports that verdict and enforces RBAC, signatures, packet integrity, expiry, and runtime lifecycle transitions.

## Non-goals

- No HTTP server is required.
- No Telegram bot or BotFather token is required.
- No hosted identity provider is required.
- The review agent must not execute the requested action.
- The packet is tamper-evident local evidence, not a remote transparency log.

## Packet directory

```text
operator-packet/
  manifest.json
  review-packet.md
  suggested-agent-prompt.md
  approval-request.json
  evidence-bundle.json
  policy-decision.json
  rollback-plan.json
  timeline.jsonl
  approval-command.sh
```

## Manifest

`manifest.json` is the import root of trust for the local packet. It records:

- `schema_version`: `agent-operator-packet.v1`
- `packet_id`
- `approval_id`
- `run_id`
- `created_at`
- `expires_at`
- `artifacts[]` with path, kind, size, and SHA-256 digest
- optional `packet_hash` over a canonical representation of artifact digests

Import must fail closed if any listed artifact is missing, changed, or has an unexpected digest.

## Human-readable packet

`review-packet.md` should be readable in a terminal or pasted into an agent. It must separate:

1. decision required;
2. proposed action facts;
3. risk and effect classification;
4. evidence list;
5. rollback/compensation summary;
6. reject criteria;
7. required verdict schema.

All untrusted fields from the action, subject, actor, evidence paths, or policy messages must be rendered as data, preferably in fenced code blocks or quoted inline fields.

## Suggested agent prompt

`suggested-agent-prompt.md` is intentionally strict:

- read the listed files;
- verify packet integrity conceptually and call out missing evidence;
- decide approve/reject only;
- do not execute the action;
- output exactly one JSON verdict.

Required approve verdict:

```json
{
  "decision": "approve",
  "approval_id": "appr_123",
  "reason": "...",
  "conditions": []
}
```

Required reject verdict:

```json
{
  "decision": "reject",
  "approval_id": "appr_123",
  "reason": "...",
  "blocking_findings": []
}
```

## Verdict import

`packet import-verdict` validates the packet and verdict before writing any lifecycle state:

1. parse verdict with a strict schema;
2. verify decision is `approve` or `reject`;
3. verify verdict approval id matches the manifest;
4. verify packet is not expired;
5. verify every manifest artifact digest;
6. authenticate the importing principal;
7. require `approve` permission for approve/reject imports;
8. write through the existing signed lifecycle store;
9. leave runtime execution to the existing approval gate.

The import command must not directly mutate registry rows or bypass lifecycle methods.

## Security review checklist

Reject implementation if any of these are true:

- action/subject text can inject instructions into the suggested prompt;
- packet import trusts the agent verdict without rechecking packet hashes;
- wrong approval id can approve another pending action;
- expired packet can still approve;
- viewer role can approve by importing a verdict;
- packet import writes `APPROVED` without HMAC/signing path;
- runtime can execute from imported verdict without `IN_FLIGHT` reservation;
- the same approved verdict can be replayed after execution.
