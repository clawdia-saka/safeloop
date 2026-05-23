# Operator Packet Manifest v1

`operator-packet-manifest.v1` records the local artifact hashes used to verify an operator packet after generation. It is a lightweight local evidence layer for `operator-packet-v2.md`.

The manifest is **tamper-evident local-only**, not tamper-proof. It does not add a hosted control plane, remote transparency log, external write, rollback capability, or compensation execution.

## Artifact

Default path:

```text
RUN_DIR/operator-packet-manifest.json
```

Schema version:

```text
operator-packet-manifest.v1
```

## Required top-level fields

- `schema_version`: must be `operator-packet-manifest.v1`
- `packet_path`: path to the packet relative to `RUN_DIR` when possible
- `packet_sha256`: SHA-256 digest of the packet as `sha256:<hex>`
- `generated_at`: ISO-8601 timestamp for manifest generation
- `run_id`: run id read from `run.json` when available
- `source_artifacts`: list of local source artifact hash entries
- `boundary`: product/safety boundary flags
- `verification`: local verification result object

## Source artifacts

`source_artifacts` includes known packet inputs when present, without storing raw source payloads:

- `run.json`
- `rollback-plan.json`
- `rollback-result.json` if present
- `runtime-tool-firewall.jsonl` if present
- `runtime-tool-exec.jsonl` if present
- `runtime-tool-exec/*/stdout.txt` and `stderr.txt` if present
- `external-effects.jsonl` if present
- `compensation-plan.json` if present
- `compensation-result.json` if present
- `verification/verify-artifacts-result.json` if present
- `local-anchor.json` if present
- quarantine metadata if present:
  - `quarantine/index.jsonl`
  - `quarantine/items/*/item.json`
  - `quarantine/items/*/restore-manifest.json`
  - `quarantine/items/*/audit.jsonl`

Quarantine payload bytes are intentionally excluded:

- `quarantine/items/*/payload/file`

Each source artifact entry has:

- `path`: artifact path relative to `RUN_DIR`
- `sha256`: `sha256:<hex>` when present, otherwise `null`
- `required`: boolean
- `present`: boolean

`operator-packet-manifest.json` must not be listed in its own `source_artifacts`.

## Boundary object

The manifest boundary object must include:

```json
{
  "exact_local_rollback_only": true,
  "external_exact_rollback": false,
  "external_compensation_manual_review_only": true,
  "runtime_unknown_tool_manual_review": true,
  "tamper_evident_local_only": true
}
```

Meaning:

- Exact rollback is only for covered local file changes.
- External actions remain `exact_rollback=false`.
- External side effects are compensation/manual-review only.
- Unknown runtime tool requests route to manual review.
- Local hashes make packet/source changes visible, but they do not provide tamper-proof guarantees.

## Verification object

The verification object has:

- `status`: `valid` or `invalid`
- `issues`: list of strings
- `verified_at`: ISO-8601 timestamp for the verification run

Verification recomputes:

- `packet_sha256`
- each source artifact hash

Invalid conditions include:

- packet missing
- packet hash mismatch
- required source artifact missing
- source artifact that was present at generation is now missing
- source artifact hash mismatch
- manifest listed as its own source artifact
- quarantine metadata exists but the manifest is missing its metadata evidence entries

Quarantine payload files are not source artifacts. Verification checks metadata evidence only.

Optional source artifacts that were absent at generation remain valid when still absent.

## CLI

Generate packet and manifest:

```bash
safeloop operator-packet RUN_DIR --write-manifest
```

Verify with default manifest:

```bash
safeloop operator-packet-verify RUN_DIR
```

Verify with custom manifest path:

```bash
safeloop operator-packet-verify RUN_DIR --manifest PATH
```

## Non-goals

- No git tag creation.
- No GitHub Release creation.
- No PyPI publication.
- No hosted control plane.
- No remote transparency log.
- No runtime rollback or compensation semantic changes.
- No tamper-proof claim.
