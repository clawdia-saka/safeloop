# SafeLoop compensation-result.v1

`RUN_DIR/compensation-result.json` is a local receipt for a manual operator result for an external side effect. It is an attestation artifact only: SafeLoop does not call external adapters, does not perform hosted remediation, does not change local rollback behavior, and does not claim exact rollback for external effects.

## Required fields

- `schema_version`: fixed string `compensation-result.v1`.
- `run_id`: copied from `RUN_DIR/run.json`.
- `effect_id`: an effect identifier present in `RUN_DIR/external-effects.jsonl` or an item identifier in `RUN_DIR/compensation-plan.json`.
- `status`: one of:
  - `compensation_completed`
  - `compensation_failed`
  - `ignored_by_operator`
  - `manual_review_required`
- `operator`: local operator label or identifier.
- `created_at`: ISO-8601 timestamp for receipt creation.
- `manual_operator_result`: always `true`.
- `exact_rollback`: always `false`.
- `local_rollback_applied`: always `false`.
- `external_execution_by_safeloop`: always `false`.
- `evidence`: contains either `path` or `url`, plus `quote_or_field`.

## Evidence requirement

Every result status requires evidence. Missing evidence path/url or missing `quote_or_field` is invalid and the receipt must not be written. Evidence should point to a stable operator ticket, log, external URL, or local artifact and quote a small stable field rather than embedding raw external payloads.

When operator-facing packet code consumes an existing `compensation-result.json` (including hand-written or legacy artifacts), any completed-style result (`compensation_completed`, `completed`, or `verified`) without a durable receipt/evidence reference is treated as invalid evidence. The packet must render `manual_review_required: missing compensation receipt` and block the recommended next action instead of presenting the compensation as completed.

## CLI

Create a receipt:

```bash
safeloop compensate result RUN_DIR \
  --effect-id ext-0001 \
  --status compensation_completed \
  --operator ops@example.test \
  --evidence ticket://incident/123 \
  --quote-or-field status=voided
```

The existing `safeloop compensate RUN_DIR` compensation-plan command remains unchanged.
