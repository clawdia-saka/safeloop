# SafeLoop external outbox v1

Schema: `external-outbox.v1`

SafeLoop uses the external outbox for actions that may leave the local repo but have not necessarily happened yet. It separates intent and approval from committed external effects:

- `pending`: intent recorded; no external dispatch is allowed.
- `prepared`: approval or waiver evidence is bound; one external dispatch may proceed.
- `committed`: dispatch happened and a matching `external-effects.jsonl` record exists.
- `compensated`: an operator compensation result/receipt exists.
- `manual_review`: the item is blocked or needs operator judgment.

SafeLoop does not perform network calls from the outbox. It records local evidence and lifecycle state only.

## Artifacts

Each run may include:

```text
RUN_DIR/external-outbox.json
RUN_DIR/external-effects.jsonl
RUN_DIR/compensation-plan.json
RUN_DIR/compensation-result.json
```

`external-outbox.json` records pre-dispatch and dispatch lifecycle. `external-effects.jsonl` records committed external effects only. Compensation artifacts remain separate from both.

## Item fields

- `schema_version`: always `external-outbox-item.v1`
- `outbox_id`: deterministic run-local ID, e.g. `outbox-0001`
- `run_id`: copied from `RUN_DIR/run.json`
- `phase`: one of `pending`, `prepared`, `committed`, `compensated`, `manual_review`
- `kind`, `target`, `action`: external intent, without raw payloads
- `evidence`: path/url plus `quote_or_field`
- `exact_rollback`: always `false`
- `compensation_capability`: `none`, `manual`, `best_effort`, or `verified`
- `compensation_boundary`: `compensatable_not_reversible`
- `dispatch_allowed`: `false` unless the item is `prepared`

Prepared items must also include:

- `approval_request_digest`
- `approval_status`: `approved` or `waived`
- `decision_id` or `waiver_id`

Committed items must also include:

- `external_effect_id`
- `external_ref`
- `commit_evidence`
- `compensation_status`

## CLI

Record intent without dispatch:

```bash
safeloop external outbox enqueue RUN_DIR \
  --kind webhook \
  --target https://example.test/hooks/review \
  --action send \
  --evidence outbox/webhook-intent.json \
  --quote-or-field payload_hash=sha256:intent \
  --reason "send review webhook"
```

Bind approval or waiver evidence:

```bash
safeloop external outbox prepare RUN_DIR outbox-0001 \
  --approval-request-digest sha256:approval \
  --approval-status approved \
  --decision-id decision-1
```

Record the committed external effect:

```bash
safeloop external outbox commit RUN_DIR outbox-0001 \
  --external-ref delivery_id=abc123 \
  --evidence logs/webhook-delivery.log \
  --quote-or-field delivery_id=abc123
```

List lifecycle state:

```bash
safeloop external outbox list RUN_DIR --json
```

## Hard rules

- Pending outbox items must not be treated as committed external effects.
- Prepared outbox items require approval or waiver evidence before dispatch.
- Committed outbox items must create `external-effects.jsonl` evidence.
- External effects remain `exact_rollback: false`.
- Compensation is a separate manual-review or operator-verification flow, not exact rollback.
- Evidence must be narrow references, not raw sensitive payloads.
