# SafeLoop Quorum execution API v1

## Decision

`execute-request` is a local, fail-closed adapter over SafeLoop authority. It validates a strict
request and HMAC-signed policy beneath an operator-controlled, non-symlink `policy_root`, reserves an existing signed lifecycle approval before process
spawn, calls `watch_run` with an argv array, and verifies artifacts plus the local anchor.

The canonical action digest is SHA-256 over every request field except `action_digest` and
`approval_id`. The signed approval action is `execute:<mutation_class>` and its subject is that
digest. An `APPROVED` lifecycle event must identify an actor different from `requested_by`.

Supported mutation classes are `read_only`, `repo_write`, and `shell_write`. All external,
release, database, policy, credential, and GitHub mutation classes fail closed.

## Acceptance criteria

- Authorization and approval reservation happen before child spawn.
- No shell parsing or shell execution is used.
- Paths are absolute, canonical, non-symlink directories; run root cannot be inside the repo.
- A single bounded JSON receipt or sanitized JSON error is emitted.
- Child success is not success unless artifact and anchor verification both pass.
- Request `run_id` is the actual SafeLoop run id; mismatches are not aliases or correlation ids.
- Any failure after approval reservation transitions the approval to terminal `FAILED`; it is never replayable.
- Existing CLI commands remain compatible.
