# SafeLoop 0.1.0 Control Plane MVP Spec

Status: draft implementation slice for `feat/safeloop-010-control-plane-mvp`.

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

## Non-goals for this branch

- No network service, daemon, or shared runtime lock is introduced.
- No mutation of existing journal/runtime storage schemas.
- No PR automation from this branch.
- The first production slice intentionally implements only the SQLite registry;
  HMAC validation, JSONL anchoring, and dashboard rendering remain test-scaffold
  candidates for follow-up slices.

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

## Approval signing draft

Approval records carry a canonical `signed_payload` plus a `signature` string.
The planned MVP signature format is `sha256=<hex_hmac>` using a locally managed
secret. Verification should compare with constant-time equality and include at
least action, subject, requester, status transition, and timestamp in the signed
payload.

## Dashboard safety draft

The static dashboard must HTML-escape all registry-provided values before
rendering. Regression tests should include user IDs, display names, actions, and
subjects containing `<script>`, event-handler attributes, quotes, and ampersands.
