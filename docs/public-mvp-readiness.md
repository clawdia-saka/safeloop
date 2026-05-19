# SafeLoop Public MVP Readiness Packet

This packet is a public MVP readiness review aid for SafeLoop 0.2.0. It is a docs plus local evidence gate: it can be checked on a local checkout, but it does not publish, sign, or tag a release.

## Release boundary

- Scope: SafeLoop public MVP readiness for the current source tree and `pyproject.toml` version.
- Status: review packet only; this is not a release tag and does not create a Git tag, package upload, hosted service, or deployment approval.
- Public claim: SafeLoop provides a local tamper-evident review aid for agent run artifacts and control-plane audit evidence.
- Boundary: local artifacts can help reviewers detect changes; they are not tamper-proof, not production governance, and not a time-machine.

## Local evidence gate

Run the local gate before treating this packet as ready for public MVP review:

```bash
python scripts/public_readiness.py --check
pytest -q tests/test_public_mvp_readiness_packet.py
```

Expected gate output includes:

```text
public-readiness: ok
version=0.2.0
demo-verifier=present
release-tag=not-created
```

The gate checks this packet for required release-boundary language, verifies the project version shape from `pyproject.toml`, confirms the local demo verifier commands are present, and rejects unqualified public overclaims.

## Demo verifier presence

The public MVP demo boundary is local and verifier-backed. The packet requires these CLI verifier surfaces to exist:

- `safeloop verify-artifacts <run_dir>` for local run artifact/hash-chain verification.
- `safeloop verify-anchor <run_dir>` for local anchor verification.
- `safeloop audit-control-plane-anchors --db <db> --anchors <anchors.jsonl> --output-dir <dir>` for local control-plane anchor audit evidence.

These commands support review and demos. They do not assert remote transparency, immutable timestamping, or hosted governance.

## Full demo flow

The public packet includes a single local full-flow script:

```bash
bash examples/full_demo.sh
```

The script demonstrates the current SafeLoop flow without claiming authority over external systems:

- Local change evidence: `watch-run` captures a repo file edit and checkpoint diff.
- Recovery: `rollback plan` and `rollback apply` restore the covered local file after review.
- Compensation/manual handoff: a fake outside-service log is kept as external evidence with `exact_rollback=false` and manual review required.
- Operator packet: `operator-packet.md` links the run directory, diff, verification result, rollback plan, external evidence, and the exact rollback command.
- Demo verification: `verify-artifacts` and `python scripts/public_readiness.py --check` are run as local gates.

Exact rollback is only claimed for covered local file changes inside the demo repository. Anything outside that repository remains compensation/manual-review territory.

## Version and build gates

- Version source: `pyproject.toml` (`project.version`).
- Current reviewed version: `0.2.0`.
- Build readiness boundary: this packet checks local metadata and verifier presence. A separate maintainer release flow may build distributions, sign artifacts, upload packages, or create tags.
- Community readiness files: `LICENSE`, `SECURITY.md`, `CONTRIBUTING.md`, issue templates, PR template, CI, and release workflow are part of the public repo gate.
- Release automation: `.github/workflows/ci.yml` runs tests, public readiness checks, build, and Twine metadata validation. `.github/workflows/release.yml` publishes GitHub releases and PyPI packages from matching `vX.Y.Z` tags after maintainers configure PyPI Trusted Publishing.
- Local optional build command for maintainers:

```bash
python -m build
```

Do not treat a passing packet check as a package publication or release certification.

## Claim boundary / banned public overclaims

Public material derived from this packet must keep these boundaries explicit:

- Say: "tamper-evident local artifacts" or "tamper-evident review aid".
- SafeLoop is not tamper-proof.
- SafeLoop is not production governance.
- SafeLoop is not a time-machine.
- Do not imply local JSON/JSONL artifacts are a remote transparency log, legal audit guarantee, or irreversible timestamping service.

Internal planning documents may discuss future governance or stronger transparency designs, but public MVP readiness claims must remain limited to local evidence generation and verification.

## Public readiness checklist

- [x] Readiness packet exists and is locally checkable.
- [x] Local verifier surfaces are documented.
- [x] Version metadata source is documented.
- [x] Release tagging and publication are explicitly out of scope.
- [x] Public overclaim bans are documented and test-covered.
- [x] Full demo flow covers local evidence, rollback/recovery, manual handoff, operator packet, and demo verification.

## Rollback public readiness skeleton

The public readiness skeleton for SafeLoop 0.2.0 demonstrates the local rollback workflow end to end:
watch a long-running local task, review and explain rollback groups, plan/apply rollback to start,
plan/apply selected files, plan/apply selected hunks, and run `policy-check`. The scripted demo is
`examples/rollback_selective_demo.sh`.

Boundary language for public docs: exact rollback is only claimed for covered local file changes.
External side effects require compensation or manual review and are not exact rollback. Local artifacts
are tamper-evident review aids, not tamper-proof guarantees. SafeLoop does not claim a remote
transparency log unless one is explicitly implemented and configured.
