# SafeLoop 0.1.0 Control Plane MVP Spec

Status: implemented local-demo foundation after PRs #38 and #39.

## Goals

SafeLoop 0.1.0 introduces a local-first control plane that stays isolated from
0.0.x runtime/watchdog worktrees while enabling auditable operator workflows.

MVP capabilities:

1. **SQLite registry** for control-plane users, approval requests, and external
   anchor bookkeeping.
2. **RBAC roles**: `admin`, `operator`, and `viewer`.
3. **HMAC-signed approval MVP** for actions that require explicit human consent.
4. **Local external anchor JSONL** for append-only export of registry decisions
   without network dependencies.
5. **Static dashboard MVP** that renders escaped control-plane state and includes
   explicit XSS regression coverage.

## Current implementation boundary

Implemented in the local-demo foundation:

- SQLite registry schema for users, approval requests, and reserved anchors.
- Fail-closed local RBAC helpers for `admin`, `operator`, and `viewer`.
- HMAC-SHA256 helpers for canonical approval payload signing and verification.
- Static escaped dashboard snapshot for users and approval requests.

Still follow-up / out of scope for the current slice:

- Network service, daemon, or shared runtime lock.
- Mutation of existing journal/runtime storage schemas.
- PR automation from this branch.
- External anchor JSONL export/lifecycle beyond reserved registry bookkeeping.
- Production governance claims such as hosted sessions, KMS, or multi-tenant auth.

## Registry schema v1

The registry is a separate SQLite database selected by caller path. It creates:

- `control_plane_metadata(key, value)` with `schema_version = 1`.
- `users(user_id, role, display_name)` where role is constrained to
  `admin|operator|viewer`.
- `approval_requests(approval_id, requested_by, action, subject, status,
  signed_payload, signature, created_at)` where status is constrained to
  `pending|approved|rejected|expired`.
- `external_anchors(anchor_id, kind, payload, created_at)` reserved for local
  JSONL anchor synchronization.

## RBAC policy draft

- `admin`: manage users, rotate approval secrets, approve/reject any request,
  export anchors, and view dashboard.
- `operator`: request approvals, approve/reject requests when policy permits,
  export anchors, and view dashboard.
- `viewer`: read-only dashboard and registry inspection.

Production enforcement should fail closed: unknown users and unknown roles have
no permissions.

## Approval signing

Approval records carry a canonical `signed_payload` plus a `signature` string.
The local-demo signature format is `sha256=<hex_hmac>` using a locally managed
secret. Verification compares with constant-time equality and covers a canonical
JSON payload supplied by callers.

## Dashboard safety

The static dashboard HTML-escapes all registry-provided values before rendering.
Regression tests include registry-controlled strings containing script tags,
event-handler-like text, quotes, and ampersands.
