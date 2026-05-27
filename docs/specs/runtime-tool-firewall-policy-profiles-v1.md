# SafeLoop runtime tool firewall policy profiles v1

Schema: `runtime-tool-firewall-policy-profiles.v1`

This document defines named policy profiles for the runtime tool firewall. A profile is a local routing and evidence posture for agent tool requests. It does not create a sandbox, grant network authority, dispatch external actions, or make external work exactly reversible.

The base firewall contract remains `runtime-tool-firewall-route.v1`. Profiles only tighten how route defaults, guarded execution, and PATH shims are selected and how the run is explained to an operator.

## Profile IDs

SafeLoop reserves three built-in profile IDs:

- `strict-local`
- `agent-dev`
- `ci-readonly`

Unknown profile IDs must fail closed before any guarded execution. If an old run has no profile metadata, operator-facing artifacts may surface the compatibility default as `strict-local` while preserving the original run metadata.

## Shared Rules

All profiles inherit these rules:

- external dispatch stays `false` from firewall routing
- external actions remain `exact_rollback: false`
- unknown tool semantics route to manual review
- guarded execution never uses `shell=True`
- dry-run classification never creates quarantine, outbox, exec, or firewall artifacts
- secrets and raw sensitive payloads are not stored in route, exec, shim, or packet metadata
- PATH shims are only a command-name fence; absolute executable paths and already-running processes can bypass them

## Profiles

### strict-local

Use `strict-local` when a run must stay inside the local workspace unless a human explicitly handles the request.

Routes:

| Request class | Route |
|---|---|
| recognized read-only local inspection | `allow_read_only` |
| destructive or local mutation | `quarantine` when SafeLoop can retain a local payload; otherwise `manual_review` |
| external write, send, publish, upload, deploy, payment, GitHub, messaging, email, webhook, or network request | `external_outbox` with `dispatch_allowed: false` |
| unknown semantics | `manual_review` |

Guarded execution:

- execute only the read-only allowlist after a successful `allow_read_only` route
- block all write-like, shell-like, package-manager, network, and external-service requests from guarded execution
- require `--workspace-root` for path-like targets and command arguments

Shim coverage:

- use shim coverage v2 for mutation tools, external tools, and command runners
- treat missing shim metadata as a packet warning when `--tool-shims` was requested

### agent-dev

Use `agent-dev` for normal local development with an AI agent. This is the closest named profile to the existing runtime firewall default route behavior.

Routes:

| Request class | Route |
|---|---|
| recognized read-only local inspection | `allow_read_only` |
| destructive or local mutation | `quarantine` when SafeLoop can retain a local payload; otherwise `manual_review` |
| external write, send, publish, upload, deploy, payment, GitHub, messaging, email, webhook, or network request | `external_outbox` with `dispatch_allowed: false` |
| unknown semantics | `manual_review` |

Guarded execution:

- execute only the read-only allowlist after a successful `allow_read_only` route
- record blocked guarded executions in `runtime-tool-exec.jsonl`
- keep outbox items local and pending until separate approval or waiver evidence is bound

Shim coverage:

- use shim coverage v2 when `--tool-shims` is enabled
- surface the v2 coverage set and bypass caveat in the operator packet

### ci-readonly

Use `ci-readonly` for CI evidence collection where SafeLoop should inspect, verify, and report without changing the workspace or creating external dispatch intent.

Routes:

| Request class | Route |
|---|---|
| recognized read-only local inspection | `allow_read_only` |
| destructive or local mutation | `manual_review` |
| external write, send, publish, upload, deploy, payment, GitHub, messaging, email, webhook, or network request | `manual_review` |
| unknown semantics | `manual_review` |

Guarded execution:

- execute only read-only inspection commands needed for verification and packet generation
- do not create quarantine items as the normal response to mutation requests
- do not create external outbox items as the normal response to external requests

Shim coverage:

- use shim coverage v2 for mutation tools, external tools, and command runners when a watched CI command is fenced
- mark any attempted write-like request as manual review evidence, not as a successful CI operation

## Shim Coverage v2

Shim coverage v2 is the named PATH-shim coverage set for profile-aware runs. It expands the v1 common risky tool set and records the coverage version in shim metadata.

The v2 set is grouped by intent:

| Group | Commands |
|---|---|
| local mutation | `rm`, `mv`, `cp`, `mkdir`, `rmdir`, `touch`, `chmod`, `chown` |
| external or hosted services | `curl`, `wget`, `gh`, `git` |
| command runners | `python`, `python3`, `node`, `npm`, `npx`, `pnpm`, `yarn`, `bun`, `sh`, `bash`, `zsh` |

Expected metadata:

```json
{
  "schema_version": "tool-shims.v2",
  "enabled": true,
  "coverage_version": "v2",
  "policy_profile": "agent-dev",
  "tools": ["rm", "mv", "cp", "mkdir", "rmdir", "touch", "chmod", "chown", "curl", "wget", "gh", "git", "python", "python3", "node", "npm", "npx", "pnpm", "yarn", "bun", "sh", "bash", "zsh"],
  "bypass_caveat": "PATH shims intercept command-name lookups only."
}
```

A profile may enable a narrower subset for platform availability, but the metadata must make missing or unsupported tools explicit. The operator packet must not imply full v2 coverage when the shim metadata is missing, downgraded, or partial.

## Operator Packet Surfacing

Operator packet v2 should surface the profile posture in the run summary and keep the firewall rows blocker-first.

Operator packet fields when metadata exists:

- `firewall policy profile`: one of `strict-local`, `agent-dev`, `ci-readonly`, or `custom`
- `tool-shims`: `enabled` or `disabled`
- `tool-shim coverage`: `v2`, `v1`, `partial`, or `not_recorded`
- `tool-shims bypass caveat`: the PATH command-name caveat

Required packet behavior:

- manual-review firewall routes appear before comforting rollback or compensation language
- partial or missing coverage is surfaced as operator evidence
- `strict-local` external requests are shown as pending external-outbox intent, not as approved external dispatch
- `ci-readonly` mutation or external requests are shown as manual review, not as successful CI activity
- operator packets never claim exact rollback for external actions

## Compatibility

Existing runs that only have `tool-shims.v1` metadata remain readable. Operator artifacts should label those runs as `tool-shim coverage: v1`; if the run did not record a profile, they may display the compatibility default as `strict-local` without mutating the original run metadata.

The profile spec is additive. Runtime implementations may keep reading `runtime-tool-firewall-route.v1`, `runtime-tool-exec.jsonl`, and `tool-shims/tool-shims.json` while adding profile and coverage fields to run metadata.
