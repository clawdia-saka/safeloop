# SafeLoop external side effect v1

Schema: `external-side-effect.v1`

SafeLoop uses this registry for actions outside the local repo. These actions are tracked for compensation and manual review only. They are never represented as covered local file rollback and never receive an exact rollback claim.

## Artifact

Each run may include:

```text
RUN_DIR/external-effects.jsonl
```

Each line is one JSON object. Effect IDs are deterministic within the run:

```text
ext-0001
ext-0002
...
```

## Required fields

- `schema_version`: always `external-side-effect.v1`
- `effect_id`: deterministic run-local ID, e.g. `ext-0001`
- `run_id`: copied from `RUN_DIR/run.json`
- `kind`: external effect kind
- `target`: external target reference, not a raw payload
- `action`: concise action label, e.g. `sent`, `commented`, `deployed`
- `created_at`: ISO-8601 timestamp
- `exact_rollback`: always `false`
- `compensation_capability`: one of the allowed capability values
- `evidence`: path/url plus `quote_or_field`
- `status`: one of the allowed status values

## Allowed `kind` examples

- `github_pr`
- `github_issue`
- `message`
- `email`
- `webhook`
- `deploy`
- `payment`
- `unknown`

## Allowed `compensation_capability`

- `none`
- `manual`
- `best_effort`
- `verified`

## Allowed `status`

- `recorded`
- `manual_review_required`
- `compensation_planned`
- `compensation_completed`
- `compensation_failed`
- `ignored_by_operator`

## Evidence requirement

`evidence` must include either `path` or `url`, plus `quote_or_field`.

Examples:

```json
{"path":"logs/webhook.log","quote_or_field":"delivery_id=abc123"}
```

```json
{"url":"https://github.com/org/repo/pull/1","quote_or_field":"pr_number=1"}
```

Missing evidence fails validation. If evidence cannot prove the external state, the effect remains `manual_review_required` until an operator records a compensation plan/result in a later SafeLoop version.

## Hard rules

- `exact_rollback` must always be `false` for external side effects.
- External effects must not appear as covered local file rollback.
- Evidence must be a stable path or URL plus a narrow quote/field.
- Do not store raw sensitive payloads, authorization headers, API keys, tokens, passwords, or full external request/response bodies by default.
- No real external adapters are implied by this schema.
- No automatic external remediation is implied by this schema.
- No hosted control plane is implied by this schema.

## CLI

```bash
safeloop external record RUN_DIR \
  --kind webhook \
  --target URL_OR_REF \
  --action sent \
  --evidence PATH_OR_URL \
  --quote-or-field TEXT \
  --capability manual
```

Output:

```text
External side effect recorded: ext-0001
exact rollback: false
status: manual_review_required
```

The command appends to `RUN_DIR/external-effects.jsonl`.
