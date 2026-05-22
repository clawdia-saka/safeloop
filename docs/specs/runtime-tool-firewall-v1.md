# SafeLoop runtime tool firewall v1

Schema: `runtime-tool-firewall-route.v1`

The runtime tool firewall is a local default-route layer for agent tool requests before they cross a mutation boundary. It does not execute tools and it does not make network calls.

Default routes:

- destructive or local mutation requests route to `quarantine`
- external write, send, publish, upload, deploy, payment, GitHub, messaging, email, or webhook requests route to `external-outbox.json`
- unknown tool semantics route to `manual_review`
- recognized read-only requests route to `allow_read_only`

## Artifact

Each route appends one record to:

```text
RUN_DIR/runtime-tool-firewall.jsonl
```

Important fields:

- `event_id`: run-local route event ID such as `fw-0001`
- `prev_event_hash`: previous firewall route hash, or `null` for the first event
- `event_hash`: SHA-256 hash over the canonical route event without `event_hash`
- `tool`, `action`, `target`, `target_kind`: narrow references for the requested tool intent
- `route`: `allow_read_only`, `quarantine`, `external_outbox`, or `manual_review`
- `route_reason`: why the default route was selected
- `dry_run`: `false` for persisted route events
- `manual_review_required`: `true` for unknown or unroutable requests
- `exact_rollback`: `true` only when a local quarantine item was retained
- `external_dispatch_allowed`: always `false` from firewall routing
- `quarantine_item_id` or `outbox_id` when the route created a downstream artifact

Route events are appended under an inter-process file lock. Readers verify the hash chain and fail closed when an existing `runtime-tool-firewall.jsonl` line is malformed, has a mismatched `prev_event_hash`, or has a mismatched `event_hash`.

## CLI

Route a destructive local cleanup through quarantine:

```bash
safeloop firewall route RUN_DIR \
  --tool rm \
  --action delete \
  --target generated.txt \
  --workspace-root "$PWD" \
  --reason "cleanup generated artifact"
```

Route an external write intent into the outbox without dispatch:

```bash
safeloop firewall route RUN_DIR \
  --tool webhook \
  --action send \
  --target https://example.test/hooks/review \
  --reason "send review webhook"
```

Classify without writing quarantine, outbox, or firewall artifacts:

```bash
safeloop firewall route RUN_DIR \
  --tool mystery \
  --action transmogrify \
  --target opaque-ref \
  --reason "agent requested an unknown capability" \
  --dry-run \
  --strict \
  --json
```

`--strict` exits non-zero when the selected route is `manual_review`. With `--dry-run`, it still writes no artifacts.

List route events:

```bash
safeloop firewall list RUN_DIR --json
```

## Downstream Boundaries

`quarantine` uses the existing SafeLoop quarantine boundary. It captures local file or directory payloads before deletion and records restore metadata. If quarantine cannot safely capture the target, the firewall records `manual_review` instead of executing the requested mutation.

`external_outbox` uses the existing external outbox lifecycle. It records a pending intent with `dispatch_allowed: false`. Operators must bind approval or waiver evidence before any external dispatch, and committed external effects remain `exact_rollback: false`.

`manual_review` is a manual review stop state. SafeLoop records the requested tool reference and route reason but does not perform the action. Operators should either review the request directly or re-run with an explicit target kind after deciding whether quarantine or outbox is the correct boundary.

## Hard Rules

- The firewall does not execute arbitrary tools.
- The firewall does not perform network calls.
- The firewall does not store raw secrets or sensitive payloads.
- Unknown tool semantics never default to execution.
- External actions never become exact rollback.
- Dry-run classification never creates quarantine, outbox, or firewall artifacts.
