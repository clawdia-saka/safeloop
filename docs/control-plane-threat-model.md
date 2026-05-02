# SafeLoop 0.1.0 mini threat model

Scope: local-first control-plane MVP merged for SafeLoop 0.1.0.

## Assets

- Control-plane registry SQLite database.
- Local-demo token database.
- Approval records, canonical signed payloads, and HMAC signatures.
- Static dashboard HTML snapshots.
- Existing SafeLoop watchdog/runtime artifacts, which must remain isolated from control-plane storage.

## Trust boundaries

- Local process boundary: library callers construct records and choose database/key paths.
- Local filesystem boundary: SQLite files and generated HTML are protected only by host filesystem permissions.
- Human-operator boundary: approval records are intended to aid operator review, not replace policy judgment.

## Threats addressed in 0.1.0

- **Role confusion:** RBAC helpers grant only documented permissions to `admin`, `operator`, and `viewer`; viewer cannot mutate users or record approvals.
- **Raw token disclosure from the token store:** `LocalTokenStore` persists salted SHA-256 token hashes, not raw bearer tokens.
- **Approval field tampering:** HMAC verification rejects changes to signed fields and wrong keys.
- **Dashboard XSS from registry values:** static dashboard output escapes dynamic fields and has regression tests for dangerous strings.
- **Runtime/control-plane coupling risk:** the control-plane registry is a separate caller-selected SQLite database and does not change existing runtime or watchdog schemas.

## Not addressed / accepted for MVP

- Local host compromise or filesystem read/write access.
- Production-grade token lifecycle, revocation, rotation, and secret custody.
- Hosted authentication/authorization, CSRF, network transport security, or HTTP session security.
- Replay-resistant approval transition protocol.
- Remote transparency log or independent external anchoring.
- End-to-end enforcement that a SafeLoop resume/rollback command cannot run without a verified approval.

## Operator guidance

Use the 0.1.0 control plane for local demos and integration experiments. Store demo databases and HMAC keys outside shared repositories. Do not expose generated dashboard HTML as an authenticated web application without additional access control and hosting hardening.
