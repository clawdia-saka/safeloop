# Compensation adapter contract examples

SafeLoop compensation adapters describe actions outside the local repo so an operator can review a concrete mitigation path. They are not rollback adapters: every example keeps `exact_rollback: false` because SafeLoop cannot make a GitHub issue, message, email, or webhook delivery become as-if-never-happened.

The checked-in JSON fixture is [`examples/compensation_adapter_contracts.json`](../examples/compensation_adapter_contracts.json). It is intentionally local-only documentation data; it does not call GitHub, email, chat, or webhook services.

## Required fields

Each adapter example includes these contract fields:

- `adapter.name`: adapter family, for example `github-issues`, `message-email`, or `webhook`.
- `adapter.version`: contract/example version.
- `adapter.supports_idempotency`: whether the adapter can provide an idempotency key for the original action.
- `effect_class`: narrow class of action outside the local repo, such as `github_issue_comment`, `message_send`, or `webhook_delivery`.
- `phase`: when SafeLoop observed the action, usually `committed` for an action that already left the repo.
- `external_ref`: redacted reference to the created issue/comment, message, email, or delivery.
- `idempotency_key`: stable key when available; `null` when the service does not support one.
- `privacy.redaction`: redaction policy for persisted references.
- `privacy.contains_secret`: whether the persisted contract says secret material is present.
- `privacy.raw_payload_persisted`: whether the raw request body was saved. The examples keep this `false`.
- `compensation.capability`: `manual` or `best_effort` in these examples.
- `compensation.action`: concrete operator-facing mitigation action.
- `compensation.operator_note`: human-readable review instruction.
- `exact_rollback`: always `false` for actions outside the local repo.
- `manual_review_required`: always `true` in these examples.

## Example: GitHub issue/comment

Use this shape when an agent opened an issue or posted a comment before a later check failed.

- `effect_class`: `github_issue_comment`
- `external_ref`: redacted issue/comment URL
- `compensation.capability`: `best_effort`
- `compensation.action`: `close_issue_or_post_correction_comment`
- `exact_rollback`: `false`
- `manual_review_required`: `true`

The contract records that the operator can close the issue or post a correction. It does not claim the original GitHub action was erased.

## Example: message or email

Use this shape when an agent sent a chat message or email.

- `effect_class`: `message_send`
- `external_ref`: redacted message or email ID
- `compensation.capability`: `manual`
- `compensation.action`: `delete_if_supported_else_send_correction`
- `exact_rollback`: `false`
- `manual_review_required`: `true`

Some services permit deletion; others require a correction. SafeLoop records the required operator decision instead of choosing blindly.

## Example: webhook delivery

Use this shape when an agent delivered a webhook or event notification to another system.

- `effect_class`: `webhook_delivery`
- `external_ref`: redacted delivery ID
- `compensation.capability`: `best_effort`
- `compensation.action`: `send_superseding_webhook_event`
- `exact_rollback`: `false`
- `manual_review_required`: `true`

A downstream service may already have processed the original webhook, so the safe mitigation is a reviewed superseding event, not a rollback claim.
