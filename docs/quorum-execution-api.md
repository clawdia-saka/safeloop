# QuorumRouter local execution API

SafeLoop remains the authorization, audit, execution-watch, artifact-verification, and anchor
authority. QuorumRouter supplies a request; it does not supply an allow decision.

Create an unsigned policy input, then have an operator sign it into a trusted root. Agents must
not have access to the signing key:

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

For a write request, create and approve the durable signed approval with the existing lifecycle:

```python
from datetime import datetime, timezone
from pathlib import Path
from safeloop.control_plane.sqlite_lifecycle import SQLiteApprovalLifecycleStore

key = Path("/absolute/private/safeloop-signing.key").read_bytes()
store = SQLiteApprovalLifecycleStore("/absolute/private/control-plane.sqlite3", key)
now = datetime.now(timezone.utc)
store.request(
    approval_id="approval-123", requested_by="quorum-agent",
    action="execute:repo_write", subject="sha256:<request-action-digest>", created_at=now,
)
store.approve("approval-123", now=now, approved_by="human-operator")
```

The approver must differ from `requested_by`. Use a random key of at least 32 bytes, store it in
a permission-restricted local file, and never put it in the request or command line. The trusted
SafeLoop executor reads it for verification; the spawned agent process does not receive the key
or its path.

Execute:

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
