# QuorumRouter local execution API

SafeLoop remains the authorization, audit, execution-watch, artifact-verification, and anchor
authority. QuorumRouter supplies a request; it does not supply an allow decision.

Create an unsigned policy input, then have an operator sign it into a trusted root. Signing-key
bytes must never enter model prompts, action payloads, receipts, or spawned child environments:

```json
{"schema_version":"safeloop.execution-policy.v1","policy_version":"2026-07-11","policy_id":"local-quorum","mutation_classes":{"read_only":{"allow":true,"require_approval":false},"repo_write":{"allow":true,"require_approval":true},"shell_write":{"allow":true,"require_approval":true}}}
```

```bash
safeloop sign-execution-policy --input /absolute/policy-input.json \
  --policy-root /absolute/operator/policies --output quorum.json \
  --signing-key-file /absolute/private/safeloop-signing.key
```

Build the complete request object with a placeholder `action_digest`, then compute its digest
using `safeloop.execution_api.canonical_action_digest`. The digest excludes only
`action_digest` and `approval_id`, allowing an approval id to be attached after authorization.

For a write request, QuorumRouter writes the immutable request to an out-of-repo
broker directory. It does not receive the signing-key path or bytes. A distinct
operator reviews the digest and invokes the combined approval/execution command:

```bash
safeloop operator-execute \
  --request /absolute/broker/<id>.request.json \
  --expected-digest sha256:<operator-reviewed-digest> \
  --receipt /absolute/broker/<id>.receipt.json \
  --approval-db /absolute/private/control-plane.sqlite3 \
  --signing-key-file /absolute/private/safeloop-signing.key \
  --policy-root /absolute/operator/policies \
  --approved-by human-operator \
  --json
```

The command rejects caller-selected approval ids, recomputes the canonical digest,
creates a signed approval record, enforces a distinct approver, executes the exact
request, verifies artifacts and the local anchor, and atomically writes a mode-0600
receipt. The requester can only poll that receipt. Use a random key of at least 32
bytes in a permission-restricted operator file. The QuorumRouter process must run
without read access to that file.

Low-level `execute-request` remains available for a trusted co-process that already
owns the signing key. Do not expose this form to an agent/requester process:

```bash
safeloop execute-request \
  --request /absolute/request.json \
  --approval-db /absolute/private/control-plane.sqlite3 \
  --policy-root /absolute/operator/policies \
  --signing-key-file /absolute/private/safeloop-signing.key \
  --json
```

The command writes exactly one compact JSON value to stdout and returns nonzero for request,
policy, approval, execution, artifact, or anchor failure. `argv` is passed directly to `Popen`;
shell strings are not accepted. The first slice rejects `github`, `db`, `external`, `release`,
`policy`, and `credential` mutation classes.

The request `run_id` is bound directly to the generated SafeLoop run and receipt. It is not a
separate caller correlation id. Receipt status is `verified` only after child success, artifact,
anchor and expected-scope verification, and approval completion; timeout is `halted`, while other
execution or verification failures are `failed`.
