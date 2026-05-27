# SafeLoop runtime tool firewall v1

Schema: `runtime-tool-firewall-route.v1`

The runtime tool firewall is a local default-route layer for agent tool requests before they cross a mutation boundary. It does not execute tools and it does not make network calls.

Default routes:

- destructive or local mutation requests route to `quarantine`
- external write, send, publish, upload, deploy, payment, GitHub, messaging, email, or webhook requests route to `external-outbox.json`
- unknown tool semantics route to `manual_review`
- recognized read-only requests route to `allow_read_only`

These defaults match the `strict-local` policy profile. The named profile contract is documented in [`runtime-tool-firewall-policy-profiles-v1.md`](runtime-tool-firewall-policy-profiles-v1.md) and reserves `strict-local`, `agent-dev`, and `ci-readonly` for profile-aware runs.

## Artifact

Each route appends one record to:

```text
RUN_DIR/runtime-tool-firewall.jsonl
```

Important fields:

- `event_id`: run-local route event ID such as `fw-0001`
- `prev_event_hash`: previous firewall route hash, or `null` for the first event
- `event_hash`: SHA-256 hash over the canonical route event without `event_hash`
- `source`: `cli`, `api`, `runtime_helper`, or `exec_wrapper`
- `action_id`: optional `action-events.jsonl` action ID when the request is made inside an `action_span()`
- `tool`, `action`, `target`, `target_kind`: narrow references for the requested tool intent
- `route`: `allow_read_only`, `quarantine`, `external_outbox`, or `manual_review`
- `route_reason`: why the default route was selected
- `dry_run`: `false` for persisted route events
- `manual_review_required`: `true` for unknown or unroutable requests
- `exact_rollback`: `true` only when a local quarantine item was retained
- `external_dispatch_allowed`: always `false` from firewall routing
- `quarantine_item_id` or `outbox_id` when the route created a downstream artifact

Route events are appended under an inter-process file lock. Readers verify the hash chain and fail closed when an existing `runtime-tool-firewall.jsonl` line is malformed, has a mismatched `prev_event_hash`, or has a mismatched `event_hash`.

Guarded command execution appends one record to:

```text
RUN_DIR/runtime-tool-exec.jsonl
```

Important execution fields:

- `exec_id`: run-local guarded execution ID such as `texec-...`
- `firewall_event_id` and `firewall_route`: the firewall decision that preceded execution
- `status`: `executed`, `blocked`, `execution_error`, or `timed_out`
- `executed`: `true` only when SafeLoop started the subprocess
- `command`, `cwd`, `workspace_root`: the argv-form command and workspace boundary
- `stdout_path`, `stderr_path`, and stream digests when execution starts
- `block_reason` when SafeLoop refuses to execute

Execution events are also hash-chained under a file lock.

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

Execute an allowlisted read-only command after firewall routing:

```bash
safeloop firewall exec RUN_DIR \
  --tool cat \
  --action read \
  --target README.md \
  --workspace-root "$PWD" \
  --reason "inspect docs" \
  -- cat README.md
```

The exec wrapper uses argv-form subprocess execution and never uses `shell=True`. It only executes commands whose firewall route is `allow_read_only`, whose executable matches the declared `--tool`, and whose tool is in the read-only execution allowlist. Destructive/local mutation, external write/send/publish, and unknown requests are recorded but not executed. Read targets, `cwd`, and path-like command arguments must stay inside `--workspace-root`.

List execution events:

```bash
safeloop firewall exec-list RUN_DIR --json
```

## Watch-run Tool Shims

`safeloop watch-run --tool-shims` creates a run-local PATH shim directory:

```text
RUN_DIR/tool-shims/bin/
RUN_DIR/tool-shims/tool-shims.json
```

SafeLoop prepends `RUN_DIR/tool-shims/bin/` to the watched command's `PATH` and writes shim coverage v2 entries for:

- local mutation commands: `rm`, `mv`, `cp`, `mkdir`, `rmdir`, `touch`, `chmod`, `chown`
- external or hosted-service commands: `curl`, `wget`, `gh`, `git`
- command runners: `python`, `python3`, `node`, `npm`, `npx`, `pnpm`, `yarn`, `bun`, `sh`, `bash`, `zsh`

Each shim infers a narrow `tool`, `action`, `target`, and `target_kind`, then calls `safeloop firewall exec`. The exec wrapper still enforces the normal read-only execution allowlist. Requests outside that allowlist are not executed:

- destructive local requests route to quarantine
- external write/send/publish requests route to `external-outbox.json`
- unknown semantics route to manual review

The shim metadata is recorded in `tool-shims/tool-shims.json`, and operator packets surface `tool-shims: enabled` or `disabled`, `tool-shim coverage`, and the bypass caveat.

Caveat: PATH shims intercept command-name lookups only. Absolute executable paths and already-running processes can bypass them, so shims are a runtime fence around common entry points rather than a kernel sandbox.

Profile-aware runs use the companion policy profile spec to describe shim coverage v2. Operator packets should surface the active firewall policy profile, the shim coverage version, partial or missing coverage, and the same PATH bypass caveat so a reviewer can tell whether the run used `strict-local`, `agent-dev`, or `ci-readonly` posture.

## Runtime Helper

Use `firewall_preflight()` in agent/runtime code before a tool call. The helper only classifies and records the route; it does not call the tool.

```python
from safeloop import action_span, firewall_preflight

with action_span("inspect_docs", intent="read docs"):
    firewall_preflight(tool="rg", action="search", target="README.md", reason="inspect docs")
```

When `run_dir` is omitted, the helper reads `SAFELOOP_RUN_DIR` and validates `SAFELOOP_RUN_ID` against `run.json`. When called inside `action_span()`, SafeLoop sets `SAFELOOP_ACTION_ID` for the duration of the span and the firewall event records that ID. Operator packets show `action-events.jsonl` as supporting evidence for correlated firewall events.

`strict=True` raises `RuntimeToolFirewallError` when the selected route is `manual_review`. Without `dry_run`, the manual-review route event is still persisted before the exception so the operator has audit evidence. With `dry_run=True`, no artifacts are written.

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
