# Clawpatch/Qwen/SafeLoop Integration Direction

This note captures the first product-facing slice for combining Clawpatch, Qwen Red/Blue review, and SafeLoop without adding runtime behavior, releases, tags, or a hosted service.

## Product roles

- **Clawpatch = code issue lifecycle**: tracks code issue intake, candidate patch generation, patch state transitions, and advisory merge-readiness handoff.
- **Qwen Red/Blue = adversarial review quality amplifier**: can generate adversarial critiques and proposed blue-team mitigations over candidate patches, evidence, and stated risk boundaries.
- **SafeLoop = local agent-run evidence, bounded rollback, compensation/manual-review, and readiness-packet layer**: records local run evidence, verifies local tamper-evident artifacts, supports exact rollback only for covered local repo file changes captured by SafeLoop artifacts, and surfaces external effects for compensation/manual review.

Clawpatch, Qwen, and SafeLoop outputs are advisory evidence only. No Clawpatch lifecycle state, Qwen review result, SafeLoop verification result, or generated packet may by itself authorize merge, release, publish, deployment, or external remediation without explicit human maintainer/operator approval.

## First integration seam

The narrow integration seam is a review packet handoff around an existing SafeLoop run directory:

1. Clawpatch opens or advances a code issue and records the candidate patch context.
2. SafeLoop watches an instrumented local agent run and emits local evidence artifacts such as `run.json`, rollback plans/results, declared or captured external side-effect records, verification results, and operator packets.
3. Qwen Red/Blue consumes the candidate patch plus SafeLoop evidence to produce adversarial findings, blue-team responses, and residual risk notes.
4. Clawpatch uses those findings as advisory lifecycle evidence before human merge-readiness decisions.
5. SafeLoop remains the local evidence reference for generated run artifacts, local tamper-evident checks, bounded rollback claims, and manual-review/compensation boundaries for external effects.

Absence of an external-effect record is not proof that no external effect occurred. External-effect coverage depends on what the workflow explicitly declared, registered, or captured.

## Review packet contents

A future machine-readable packet should be additive to the existing operator packet artifacts and should prefer links or hashes over raw payload duplication. Minimal fields:

- `schema_version`: e.g. `clawpatch-qwen-review-packet.v1` when introduced.
- `issue`: Clawpatch issue id, title, lifecycle state, and candidate patch reference.
- `safeloop_run`: run id, run directory-relative artifact paths, and `operator-packet-manifest.v1` hash when present.
- `qwen_red_findings`: adversarial critiques with severity, affected files or behaviors, and evidence references.
- `qwen_blue_responses`: proposed mitigations, accepted/rejected findings, and follow-up actions.
- `advisory_readiness`: non-authoritative merge-readiness recommendation, required operator checks, required human approval scope, and explicit residual risks.
- `boundary`: exact rollback only for covered local repo file changes captured by SafeLoop artifacts and verified at rollback time; external effects are manual-review/compensation only; evidence is local tamper-evident, not tamper-proof or independently notarized.
- `redaction`: statement of whether secrets, credentials, private source excerpts, customer data, and raw logs were excluded or explicitly approved and redacted.

Clawpatch/Qwen inputs are untrusted data. Packet generation and verification must validate schemas, sizes, paths, URLs, encodings, and artifact references. Findings, critiques, and blue-team responses are model or tool outputs, not facts unless backed by verifiable evidence or explicit human decision.

## Non-goals for this slice

- No new release, git tag, PyPI publication, or GitHub Release.
- No hosted Clawpatch/Qwen/SafeLoop control plane.
- No autonomous merge approval.
- No new runtime rollback semantics.
- No claim that SafeLoop can exactly roll back external systems.
- No claim that Qwen Red/Blue is a security certification, formal verification pass, or substitute for maintainer/security review.
- No claim that Clawpatch guarantees issue discovery, patch correctness, absence of regressions, or vulnerability remediation.
- No tamper-proof, independently notarized, or remote transparency-log claim.
- No policy, legal, compliance, SOC2, or governance guarantee.

## Acceptance criteria for a later schema slice

- The packet can be generated from local files under a SafeLoop run directory plus explicit Clawpatch/Qwen inputs.
- The packet references `operator-packet-manifest.v1` rather than replacing it.
- Verification fails closed on missing required evidence, absolute paths, parent-directory traversal, symlink escapes, oversized artifacts, unsafe references, or mismatched artifact hashes.
- Tests cover boundary language, advisory approval semantics, required fields, redaction status, and at least one invalid packet case.
