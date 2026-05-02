# Approval lifecycle notes

This note describes the 0.1.0 control-plane approval model as implemented today and separates it from planned lifecycle work.

## Implemented record shape

Approval records are stored in the control-plane SQLite registry as immutable inserts with these statuses allowed by schema:

- `pending`
- `approved`
- `rejected`
- `expired`

Each record includes a `signed_payload` and `signature`. The HMAC covers the approval ID, action, subject, status, requester, and creation timestamp.

## Current MVP flow

1. Construct an `ApprovalRecord` with the intended status, action, subject, requester, and timestamp.
2. Sign it with `sign_approval_record(record, key)`.
3. Optionally verify it with `verify_approval_record(record, key)` before persistence.
4. Persist it with `ControlPlaneRegistry.record_approval(...)` or with `record_approval_as(...)` after RBAC enforcement.
5. Inspect persisted records with `list_approvals(...)`, `get_approval(...)`, or the static dashboard renderer.

The current helper `record_approval_as` requires the `approve` permission. Admin and operator roles have that permission; viewer does not.

## Important 0.1.0 limitation

0.1.0 does not yet implement a state-transition engine. The registry stores approval records and enforces status constraints, but it does not yet provide a first-class API that transitions an existing approval from `pending` to `approved`, `rejected`, or `expired`.

Until lifecycle APIs land, callers should treat approval records as signed audit entries and avoid implying that SafeLoop automatically gates or resumes runtime actions based on registry contents.

## Expected future lifecycle

Planned follow-up work should define explicit operations such as:

- request approval
- approve approval
- reject approval
- expire stale approval
- resume a run only after a verified approval decision
- preserve a transition history or append-only decision log

Those operations should verify the HMAC, enforce RBAC at each transition, and make replay/tamper behavior explicit.
