# Compensation is not rollback

SafeLoop separates **covered local file rollback** from **external service compensation**.

- Covered local file changes can be planned, reviewed, and rolled back when SafeLoop can verify the covered state.
- External service changes are never treated as exact rollback. They remain `exact_rollback: false` even when a cleanup or correction action is available.
- Compensation is an operator-facing mitigation plan: SafeLoop records what happened, which compensation action is possible, and whether `manual_review_required` still applies.

This boundary is the product point: SafeLoop does not pretend outside-world actions vanished.

## Required artifact semantics

Every external compensation example should preserve these semantics:

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

## Local rollback vs external compensation

- Local rollback answers: can SafeLoop restore covered local files from verified artifacts?
- External compensation answers: what should an operator do about an action that already left the repo?
- A single run may have both: local rollback succeeds while external service compensation remains pending.

That is expected. SafeLoop should surface both states instead of collapsing them into one unsafe success label.
