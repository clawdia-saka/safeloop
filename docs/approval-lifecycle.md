# Approval lifecycle

SafeLoop now has two approval layers:

- `ControlPlaneRegistry`: lookup/audit registry for users, tokens, approval rows, and events.
- `SQLiteApprovalLifecycleStore`: durable lifecycle state machine for runtime enforcement.

Runtime enforcement must use a lifecycle store. A lookup-only `ControlPlaneRegistry` is intentionally rejected by `Runtime.run(...)` for mutating actions because it cannot atomically reserve or complete approvals.

## Durable state machine

`SQLiteApprovalLifecycleStore(path, key, ttl=...)` persists the current approval state in the existing `approvals` table and appends every transition to `approval_events`.

Supported states:

- `REQUESTED`
- `APPROVED`
- `IN_FLIGHT`
- `REJECTED`
- `EXECUTED`
- `EXPIRED`
- `REVOKED`

Normal execution flow:

1. `request(...)` creates a signed `REQUESTED` approval.
2. `approve(...)` transitions `REQUESTED -> APPROVED`.
3. `reserve_for_execution(...)` verifies the presented record against the stored row, checks the HMAC, checks TTL/scope, and transitions `APPROVED -> IN_FLIGHT`.
4. `complete_execution(...)` verifies the in-flight record and transitions `IN_FLIGHT -> EXECUTED`.

Single-step consumers can call `execute_once(...)`, which verifies and transitions `APPROVED -> EXECUTED` atomically.

Reject/revoke flows:

- `reject(...)` can terminally reject pending/approved work.
- `revoke(...)` can terminally revoke requested/approved/in-flight work.
- Expired records fail closed and are durably marked `EXPIRED` when a transition/validation observes them after TTL.

## Fail-closed validation

Every transition re-reads the stored row. The store rejects:

- unknown approvals;
- stale presented records after any status change;
- tampered `signed_payload` or HMAC signatures;
- expired approvals, which are marked `EXPIRED`;
- scope mismatches for requester/action/subject;
- replay attempts after reservation or execution.

The signed payload remains deterministic JSON over approval ID, action, subject, status, requester, and creation timestamp.

## Restart behavior

The lifecycle store is SQLite-backed. `APPROVED`, `IN_FLIGHT`, and `EXECUTED` states survive process restarts. Runtime startup can reserve a previously approved record after restart, and resume validation can verify a persisted `IN_FLIGHT` approval when the runtime still has the checkpoint needed to resume safely.

## Boundary notes

This work does not add hosted auth, KMS, remote transparency logs, or rollback CLI changes. It is a local durable lifecycle foundation and runtime enforcement path.
