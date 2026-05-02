# SafeLoop 0.1.0 release notes

SafeLoop 0.1.0 introduces the first local control-plane MVP while preserving the existing watchdog/runtime artifact boundaries.

## Highlights

- Added an isolated SQLite control-plane registry with schema versioning for users, approval requests, and reserved external anchors.
- Added local-demo RBAC roles: `admin`, `operator`, and `viewer`.
- Added local token storage that persists salted token hashes rather than raw bearer tokens.
- Added HMAC signing and verification for approval records using deterministic canonical payloads.
- Added a static dashboard renderer for users and approvals with HTML escaping and XSS regression coverage.

## Documentation

- [`control-plane.md`](control-plane.md) explains implemented 0.1.0 surfaces and non-goals.
- [`approval-lifecycle.md`](approval-lifecycle.md) documents the current signed-record model and clearly marks lifecycle transitions as follow-up work.
- [`control-plane-threat-model.md`](control-plane-threat-model.md) captures MVP assets, boundaries, covered threats, and accepted risks.
- [`../examples/control-plane-local-demo/README.md`](../examples/control-plane-local-demo/README.md) provides a copy-pasteable local demo.

## Compatibility and migration

0.1.0 does not require migration of existing SafeLoop watchdog or runtime artifacts. The control-plane registry is created at a path selected by the caller and is separate from existing run directories and journals.

## Known limitations

- No hosted control-plane daemon or HTTP API.
- No production identity provider or secret-management integration.
- No first-class approval lifecycle transition API yet.
- No external anchor export/verification flow yet, although the registry reserves an `external_anchors` table.
- No dashboard v2 or authenticated browser workflow.
- No automatic enforcement that runtime resume/rollback commands require verified approvals.

## Verification

The 0.1.0 slice is covered by tests for registry schema behavior, RBAC/local token checks, HMAC tamper detection, and escaped static dashboard rendering.
