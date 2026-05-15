# SafeLoop v0.2.1 release notes candidate

release action: HOLD

This document is a release-notes candidate for the current SafeLoop v0.2.1 posture. It summarizes the hardening work that is ready for public-readiness messaging, but it does not authorize tag creation, does not authorize PyPI publish, and does not authorize a GitHub Release.

No tag / GitHub Release / PyPI action should happen without explicit TT approval.

## Summary

SafeLoop v0.2.1 candidate closes the remaining public-readiness hardening gaps around external-action evidence, operator packet consistency, and local packet integrity verification. The candidate keeps SafeLoop's recoverability-first boundary intact: local rollback is exact only for covered local file changes, while actions outside the local repo remain evidence-backed compensation/manual-review flows.

## What changed

- **operator packet status drift fix**
  - Operator packet rows now stay coherent after verified manual compensation.
  - Completed compensation no longer leaves sibling manual-review or compensation rows in stale queued/review-required states.

- **install clean-env smoke hardening**
  - Clean-install smoke coverage was hardened around post-rollback artifact verification semantics.
  - The release candidate can be checked from a clean source tree without treating release publication as part of readiness.

- **external effect registry compatibility / exact rollback overclaim fix**
  - Legacy and current external effect registry inputs remain visible to the operator packet path.
  - Invalid external registry compensation plans are blocked instead of masking overclaims.
  - exact rollback overclaim handling remains explicit: external actions are never promoted to exact rollback.

- **compensation evidence required gate**
  - Operator packets require receipt-backed evidence before showing compensation as complete.
  - Missing compensation receipts remain an action-required condition instead of a green state.

- **operator packet manifest verification**
  - The candidate adds local `operator-packet-manifest.v1` verification for `operator-packet-v2.md` and its source artifacts.
  - This gives local tamper-evident verification for packet/source hashes.

- **public readiness release gate**
  - The v0.2.1 readiness note records that public-readiness messaging is OK, while release actions remain HOLD.
  - `python scripts/public_readiness.py --check` remains the local readiness gate and does not publish, sign, or tag anything.

- **packet_hash_verify_design_gap closed**
  - The prior `packet_hash_verify_design_gap` is closed by the local operator packet manifest and `safeloop operator-packet-verify` flow.
  - The closure is local-only evidence hardening; it is not a hosted transparency system.

## Boundary notes

- exact rollback only applies to covered local file changes.
- external actions remain exact_rollback=false.
- external side effects are compensation/manual-review only.
- operator packet manifest verification is local tamper-evident verification, not tamper-proof.
- no hosted control plane.
- no remote transparency log.
- no tag / GitHub Release / PyPI without explicit TT approval.

## What intentionally remains out of scope

- Creating a `v0.2.1` git tag.
- Creating a GitHub Release.
- Publishing to PyPI.
- Changing runtime behavior.
- Changing rollback or compensation semantics.
- Adding new external adapters.
- Shipping a hosted control plane.
- Shipping a remote transparency log.

## Verification expected for this candidate

Run these gates before treating the candidate as current:

```bash
pytest -q tests/test_v021_release_notes_candidate.py
pytest -q tests/test_operator_packet_manifest.py tests/test_v021_readiness_note.py
pytest -q
git diff --check
python scripts/public_readiness.py --check
python -m build
```

These commands validate the local source tree and build artifacts only. They do not authorize release publication.
