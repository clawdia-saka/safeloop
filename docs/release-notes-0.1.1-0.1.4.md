# SafeLoop 0.1.1-0.1.4 release notes

Status: local control-plane hardening slices, implemented as a local-demo foundation.

## 0.1.1 Runtime approval gate foundation

- Adds `safeloop.control_plane.gate.require_approval(...)`.
- Validates approval id, approved status, HMAC signature, action, and subject.
- Delegates to the approval lifecycle store when provided so expiry, revocation, and replay protections remain fail-closed.
- Current boundary: this is a library gate foundation. Existing runtime commands are not globally forced through the gate unless a caller wires it into that path.

## 0.1.2 Anchor verification and audit packet

- Adds `safeloop.control_plane.anchor_audit` for tamper-evident local anchor audits.
- Adds CLI: `safeloop audit-control-plane-anchors --db DB --anchors anchors.jsonl --output-dir audit/`.
- Emits `control-plane-audit.json` and `control-plane-audit.md`.
- Detects digest mismatch, missing anchors, reordered anchors, stale anchors, malformed JSONL, unexpected anchors, and missing/mismatched per-record hashes.
- Current boundary: this is local JSONL audit, not a remote transparency log or tamper-proof timestamping service.

## 0.1.3 Static dashboard operator UX

- Extends static dashboard v2 with approval status counts, evidence fields, evidence links, rollback blockers, side-effect status, anchor verification status, and why-blocked reasons.
- Keeps dynamic fields HTML-escaped.
- Blocks dangerous evidence link schemes such as `javascript:` and `data:`; allows local/static-demo schemes `file`, `http`, `https`, and relative links.
- Current boundary: static render-only HTML; no hosted sessions or authenticated browser workflow.

## 0.1.4 Policy profiles

- Adds JSON/YAML policy profile loading via `safeloop.control_plane.policy_profiles`.
- Enforces role, permission, approval status, max approval age, anchor verification, and allowed rollback tiers.
- Unknown policies/actions, malformed configs, and version downgrades fail closed.
- Current boundary: library/profile enforcement foundation. Operators must explicitly load and apply profiles in the runtime/control-plane path they are gating.

## Verification snapshot

Known-good combined branch verification:

```text
python -m pytest tests/ -q
147 passed

python -m pytest tests/test_control_plane_*.py -q
36 passed

python examples/github_pr_demo.py
Closed PR #101 for nous/safeloop
compensated
True
```
