# Compensation is not rollback

SafeLoop separates **covered local file rollback** from **compensation for actions outside the local repo**.

- Covered local file changes can be planned, reviewed, and rolled back when SafeLoop can verify the covered state.
- Actions outside the local repo are never treated as exact rollback. They remain `exact_rollback: false` even when a cleanup or correction action is available.
- Compensation is an operator-facing mitigation plan: SafeLoop records what happened, which compensation action is possible, and whether `manual_review_required` still applies.
- Manual handoff is the expected next step when the compensation action needs human judgment, credentials, or verification in the outside system.

This boundary is the product point: SafeLoop does not pretend actions outside the local repo vanished.

## Required artifact semantics

Every compensation example for actions outside the local repo should preserve these semantics:

```yaml
exact_rollback: false
status: manual_review_required | operator_review_required | blocked
compensation_action_recorded: true
warnings:
  - manual_review_required  # when an operator must perform the compensation
  - operator_review_required: external compensation requires operator verification
```

`compensation_action_recorded` means the plan names a concrete cleanup/correction path. In prose: a compensation action recorded entry is evidence for review, not proof that SafeLoop executed it. It does **not** mean SafeLoop executed that path or proved the external system returned to an as-if-never-happened state.

## Example 1: GitHub issue/comment

A coding agent may create a GitHub issue or post a comment before a later check fails. SafeLoop should not claim local rollback removed that GitHub artifact.

Operator packet shape:

- Side effect: `GitHub issue/comment`
- External reference: issue/comment URL or redacted ID
- Compensation action recorded: close the issue, edit the comment, or post a correction comment
- Capability: `best_effort`
- Boundary: `exact_rollback: false`
- Review: operator verifies whether the close/edit/correction is acceptable

Run the local-only fixture demo:

```bash
bash examples/compensation_github_issue_demo.sh
```

The script writes a run-local `side-effects.jsonl` and `compensation-plan.json`; it does not call GitHub.

## Example 2: message correction

A notification agent may send a Telegram, Slack, or email message. Some platforms allow deletion; others require a correction message.

Operator packet shape:

- Side effect: `message_send`
- External reference: redacted channel/message ID
- Compensation action recorded: delete if supported, else send correction
- Capability: `manual`
- Boundary: `exact_rollback: false`
- Review: `manual_review_required` because the operator must verify platform behavior and audience impact

Run the local-only fixture demo:

```bash
bash examples/compensation_message_demo.sh
```

## Local rollback vs compensation for actions outside the local repo

- Local rollback answers: can SafeLoop restore covered local files from verified artifacts?
- Compensation for actions outside the local repo answers: what should an operator do about an action that already left the repo?
- A single run may have both: local rollback succeeds while compensation for a GitHub issue, message, email, or webhook remains pending.

That is expected. SafeLoop should surface both states instead of collapsing them into one unsafe success label.

## Fake external webhook compensation demo

`examples/fake_external_webhook_compensation_demo.py` is a deterministic fake external webhook compensation demo. It is fully fake/local/offline and does not contact third-party systems, dispatch webhooks, use a hosted control plane, perform automatic remediation, or change local rollback behavior.

Run it locally:

```bash
python examples/fake_external_webhook_compensation_demo.py
```

The demo writes only local artifacts:

- `external-effects.jsonl`: a fake/local webhook effect with `exact_rollback: false`, `manual_review_required: true`, and `evidence_required: true`.
- `manual-compensation-result.json`: a fake/manual compensation result for operator review.
- `operator-packet.md`: a packet that separates local rollback from external manual review and states the external action is not exact rollback.

## External side-effect registry

SafeLoop v0.2 tracks actions outside the local repo in `RUN_DIR/external-effects.jsonl` using `external-side-effect.v1` records. These records are for compensation/manual review only: every external effect keeps `exact_rollback: false`, requires evidence with a path or URL plus `quote_or_field`, and must not store raw sensitive payloads. See `docs/specs/external-side-effect-v1.md`.

## External outbox lifecycle

SafeLoop v3 quarantine hardening adds `RUN_DIR/external-outbox.json` for actions that may leave the local repo but have not necessarily happened yet. The outbox separates:

- `pending`: intent recorded, no dispatch allowed.
- `prepared`: approval or waiver evidence is bound, dispatch may proceed once.
- `committed`: the external action happened and a matching `external-effects.jsonl` record exists.
- `compensated`: compensation result/receipt evidence exists.
- `manual_review`: blocked or operator judgment required.

This is still local evidence only. SafeLoop does not perform network calls, does not claim external exact rollback, and keeps compensation in a separate plan/result flow. See `docs/specs/external-outbox-v1.md`.

## Compensation plan v1

`safeloop compensate RUN_DIR` writes `RUN_DIR/compensation-plan.json` with `schema_version: compensation-plan.v1`. When `RUN_DIR/external-effects.jsonl` is present, the plan is generated from that registry instead of legacy `side-effects.jsonl` records.

Plan boundaries:

- Plan-level `exact_rollback` is always `false`.
- Each item refers to an external effect by `external_effect_id` / `effect_id` and `evidence_ref: external-effects.jsonl#<effect_id>`.
- SafeLoop does not execute adapters, make network calls, or remediate automatically (`external_execution: false`, `network_calls: false`).
- Missing external evidence blocks planning with `missing_external_evidence` and keeps `manual_review_required`; it is never upgraded to exact rollback.
- Capabilities (`none`, `manual`, `best_effort`, `verified`) describe operator mitigation confidence only, not an as-if-never-happened guarantee.
