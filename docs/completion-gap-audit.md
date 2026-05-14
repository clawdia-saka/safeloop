# SafeLoop completion gap audit

This audit accompanies the v0.2 readiness matrix and `docs/v0.2.0-rc-definition-of-done.md`. It lists what is complete, what is partial, what is planned, and what is intentionally out of scope so SafeLoop does not overstate readiness.

## Audit principle

SafeLoop should be described as a local recoverability and evidence packet system unless a future PR explicitly adds hosted control-plane, remote transparency, or production signing infrastructure. Exact rollback is limited to covered local file changes. Actions outside the local repo are compensation/manual-review territory.

## Complete for v0.2 local scope

- **Artifact verification**
  - Evidence: `safeloop verify-artifacts`, `scripts/public_readiness.py`, `examples/full_demo.sh`, `tests/test_public_mvp_readiness_packet.py`.
  - Gap: none for local demo artifacts.

- **Timeline/explain UX**
  - Evidence: `safeloop timeline`, `safeloop explain`, `src/safeloop/rollback_groups.py`, `tests/test_rollback_groups.py`, `docs/recoverability-first.md`.
  - Gap: none for operator-language local review.

- **Rollback plan/apply and selective rollback**
  - Evidence: `safeloop rollback plan`, `safeloop rollback apply`, `examples/rollback_selective_demo.sh`, `examples/full_demo.sh`, `tests/test_action_rollback.py`, `tests/test_selective_file_rollback.py`, `tests/test_hunk_rollback.py`.
  - Gap: exact rollback applies only to covered local file changes.

- **Compensation adapter contract examples and fake demo**
  - Evidence: `docs/compensation-adapter-contracts.md`, `examples/compensation_adapter_contracts.json`, `examples/recoverability_external_effect_demo.sh`, `tests/test_compensation_examples_docs.py`, `tests/test_examples_boundary_demos.py`.
  - Gap: examples define the contract and fake/local demo shape; they do not execute real third-party compensating actions.

- **Compensation plan/result evidence**
  - Evidence: `src/safeloop/compensation.py`, `docs/compensation.md`, `tests/test_compensation_planner.py`, `tests/test_compensation_examples_docs.py`.
  - Gap: plans and results are operator evidence for manual review, not automatic external remediation.

- **External side-effect registry evidence**
  - Evidence: `src/safeloop/side_effect_ledger.py`, `src/safeloop/external_effects.py`, `docs/specs/external-side-effect-v1.md`, `tests/test_external_effects.py`, `tests/test_side_effect_ledger_runtime_005.py`.
  - Gap: registry/ledger entries document external effects; they do not make external effects exactly rollbackable.

- **Manual review boundary**
  - Evidence: `docs/compensation.md`, `docs/rollback.md`, `docs/recoverability-first.md`, `examples/full_demo.sh`, `tests/test_recoverability_story_docs.py`.
  - Gap: none for v0.2 wording and demos.

- **Operator packet and full demo flow**
  - Evidence: `docs/specs/operator-packet-v1.md`, `examples/gbrain_context_demo.sh`, `examples/full_demo.sh`, `tests/test_gbrain_context_demo.py`, `tests/test_public_mvp_readiness_packet.py`.
  - Gap: operator packet is currently a spec/demo artifact, not a standalone product API.

- **Public readiness script**
  - Evidence: `scripts/public_readiness.py`, `tests/test_public_mvp_readiness_packet.py`.
  - Gap: release-tag creation is reported but not required by the local readiness gate.

## Partial and planned capabilities

- **Long-running task watchdog: partial**
  - Evidence: `src/safeloop/agent_watchdog.py`, `examples/watchdog_demo.sh`, `tests/test_agent_watchdog_rc.py`, `tests/test_watchdog_reliability_004.py`.
  - Gap: local watchdog behavior exists, but hosted alerting, scheduling, and multi-tenant operational supervision are not complete.
  - Next PR: product/operations watchdog hardening if required.

- **Local tamper-evident guarantees: partial**
  - Evidence: local event hashes from `verify-artifacts`, `src/safeloop/local_anchor.py`, `tests/test_local_anchor.py`, `tests/test_control_plane_anchor_audit.py`.
  - Gap: local hashes and anchors are not the same as a remote transparency log or public notarization.
  - Next PR: remote transparency/signing design if required.

- **Remote transparency/signing status: planned**
  - Evidence: control-plane threat-model docs and anchor-related tests show direction.
  - Gap: no hosted append-only transparency service, public log, or production signing network is shipped.
  - Next PR: design and scope PR before implementation.

## Intentionally out of scope for v0.2

- **Hosted control plane**
  - Status: `out_of_scope` for v0.2.
  - Evidence: `docs/control-plane.md` and control-plane tests exist as local/product-direction artifacts, but they are not a hosted production control plane.
  - Boundary: do not claim hosted multi-user control-plane readiness in v0.2.

- **External side-effect exact rollback**
  - Status: `out_of_scope` for v0.2 and intentionally not promised.
  - Evidence: `docs/compensation.md`, `docs/compensation-adapter-contracts.md`, `examples/compensation_adapter_contracts.json`, `examples/full_demo.sh`.
  - Boundary: actions outside the local repo—GitHub, messaging, email, webhook delivery, hosted systems, and similar remote services—must be handled through compensation and manual review. SafeLoop never claims exact rollback for those actions.

- **Real external adapters**
  - Status: `out_of_scope` for v0.2.
  - Boundary: v0.2 ships contracts and fake/demo artifacts only; it does not ship production adapters for real third-party services.

- **Automatic external remediation**
  - Status: `out_of_scope` for v0.2.
  - Boundary: compensation plans/results are operator evidence for manual remediation; SafeLoop does not automatically remediate external systems.

## Completion risk notes

- The public story should emphasize recoverability and operator evidence, not remote invulnerability.
- `complete` means complete for local v0.2 scope, not complete for every future hosted product shape.
- `planned` and `out_of_scope` items should stay visible in docs so future readers do not infer hidden guarantees.
- The phrase “exact rollback” should remain tied to covered local file changes only.
