# SafeLoop 0.1.0 control-plane MVP

SafeLoop 0.1.0 adds a local-first control-plane MVP on top of the existing watchdog/runtime work. The merged 0.1.0 slice is intentionally small: it provides a separate SQLite registry, local-demo token/RBAC helpers, HMAC signing for approval records, and a static escaped dashboard renderer.

This is not a hosted service. It is a library-level foundation for auditable operator workflows and demos while keeping runtime journals and watchdog artifacts isolated.

## Implemented in 0.1.0

### Isolated SQLite registry

`ControlPlaneRegistry(path)` creates and uses a caller-selected SQLite database. It does not mutate existing SafeLoop run directories, journals, or watchdog artifact schemas.

Schema version `1` includes:

- `control_plane_metadata(key, value)` with `schema_version = 1`
- `users(user_id, role, display_name)`
- `approval_requests(approval_id, requested_by, action, subject, status, signed_payload, signature, created_at)`
- `external_anchors(anchor_id, kind, payload, created_at)` reserved for future anchor export flows

### Local-demo RBAC and tokens

`LocalTokenStore` issues local bearer tokens and stores only salted SHA-256 token hashes. It is suitable for demos and tests, not a production identity provider.

Roles and permissions:

| Role | Permissions |
| --- | --- |
| `admin` | `view`, `request_approval`, `approve`, `reject`, `resume`, `manage_users`, `rotate_secrets`, `export_anchors` |
| `operator` | `view`, `request_approval`, `approve`, `reject`, `resume`, `export_anchors` |
| `viewer` | `view` |

`require_permission(principal, permission)` fails closed for disallowed role/permission pairs. Authorized helper functions currently cover user upsert, approval recording, and approval listing.

### HMAC-signed approval records

Approval records can be signed with `sign_approval_record(record, key)` and checked with `verify_approval_record(record, key)`.

The signature format is:

```text
sha256=<hex hmac>
```

The canonical signed payload is deterministic JSON over:

- `approval_id`
- `action`
- `subject`
- `status`
- `requested_by`
- `created_at`

Verification uses constant-time comparisons for both the canonical payload and HMAC signature and rejects tampering of any signed field or use of the wrong key.

### Static dashboard snapshot

`render_static_dashboard(registry)` returns static HTML for users and approvals. Registry-provided values are HTML-escaped before rendering, with regression coverage for script tags, event-handler attributes, quotes, and ampersands.

The dashboard is a local snapshot renderer only. It does not implement hosting, sessions, browser-side authorization, live updates, or dashboard v2 workflows.

## Minimal local usage

```python
from safeloop.control_plane.auth import LocalTokenStore
from safeloop.control_plane.dashboard import render_static_dashboard
from safeloop.control_plane.registry import ApprovalRecord, ControlPlaneRegistry, RegistryUser
from safeloop.control_plane.signing import sign_approval_record, verify_approval_record

registry = ControlPlaneRegistry("./control-plane.sqlite3")
registry.initialize()
registry.upsert_user(RegistryUser("ada", "admin", "Ada Admin"))

tokens = LocalTokenStore("./tokens.sqlite3")
admin_token = tokens.issue_token("ada", "admin")
principal = tokens.authenticate(admin_token)

approval = ApprovalRecord(
    approval_id="appr-1",
    requested_by="ada",
    action="resume_run",
    subject="runs/run-123",
    status="pending",
    signed_payload="",
    signature="",
    created_at="2026-05-03T03:00:00Z",
)
signed = sign_approval_record(approval, b"local-demo-secret")
assert verify_approval_record(signed, b"local-demo-secret")
registry.record_approval(signed)

html = render_static_dashboard(registry)
```

See [`../examples/control-plane-local-demo/README.md`](../examples/control-plane-local-demo/README.md) for a fuller local demo.

## Explicit non-goals in this release

- No hosted control-plane daemon or HTTP API.
- No production identity provider, session management, OAuth/OIDC, or secret-management integration.
- No mutation of existing watchdog/runtime journal schemas.
- No automatic lifecycle state transitions beyond inserting approval records.
- No network transparency log or remote anchoring.
- No dashboard v2, live UI, or browser-side authorization.

## Next / PR-linked work

These items are intentionally documented as next work unless and until separately merged. The primary direction is now agent-native rather than HTTP- or Telegram-first; see [`roadmap.md`](roadmap.md) and [`specs/agent-native-operator-packet.md`](specs/agent-native-operator-packet.md).

- 0.1.5 release alignment for the runtime-enforced approval lifecycle and static-dashboard-as-artifact positioning.
- 0.2.0 agent-native operator packets: `review-packet.md`, `suggested-agent-prompt.md`, machine-readable evidence, manifest hashes, and strict verdict import.
- Agent prompt exporters for Hermes, Claude Code, Codex, and OpenClaw.
- Operator inbox CLI for pending approvals and evidence review.
- Optional later transports: localhost HTTP viewer or Telegram ChatOps, both reusing the same packet/verdict/lifecycle code paths rather than becoming separate approval authorities.
- Production secret rotation and pluggable identity providers, after the local packet flow is proven.
