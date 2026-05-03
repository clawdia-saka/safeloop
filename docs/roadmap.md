# SafeLoop roadmap: agent action time-machine

Status: direction reset after 0.1.x control-plane/runtime-enforcement work and TT feedback.

SafeLoop's next product direction is **retrospective control for long-running agents**. The core value is not making agents stop more often. The core value is letting OpenClaw, Hermes, Codex, Claude Code, or local runners continue useful work while SafeLoop records enough timeline, evidence, policy, rollback, and decision context for an operator to later extract only the deltas, gaps, bypasses, and unverified claims.

HTTP dashboards, Telegram bots, and hosted control planes are optional viewers/transports. The default product surface is an agent-native evidence packet plus a delta-only operator brief.

## Product thesis

The user should be able to say: "run this overnight / finish this release train / open-review-merge-and-continue," then inspect only what matters afterward.

SafeLoop should support this loop:

1. an agent pursues a long-running objective across PRs, slices, retries, and checkpoints;
2. SafeLoop captures a monotonic timeline of goals, actions, decisions, artifacts, approvals, tests, PR state, and side effects;
3. low-risk work continues in Auto/Shadow mode, while irreversible or high-risk actions pause only when policy requires it;
4. after the run, SafeLoop emits a delta-only packet showing what changed since the last checkpoint or review;
5. a human or review agent can extract gaps, skipped verification, semantic duplicates, policy drift, and rollback/resume options;
6. approvals/verdicts still flow through the existing RBAC, signing, replay-protected lifecycle path when execution crosses a guarded boundary.

This makes SafeLoop an **agent action time-machine**: not a chat UI, not a hosted dashboard, and not a generic task runner.

## Operating modes

### Auto / Shadow

Default for long local work, reversible file edits, test execution, local build loops, read-only research, and PR preparation.

SafeLoop records actions, artifacts, policy classifications, and checkpoints without interrupting the agent. Notifications are delta-only and should suppress green baselines.

### Guarded

Default for external writes, PR merge, release publishing, outbound messages, production deploys, repo permission changes, and anything that has real external side effects.

SafeLoop may pause, generate an operator packet, require a verdict, and then let the runtime consume an approval exactly once.

### Strict

Default for funds, secrets, production credentials, security-critical auth changes, destructive data actions, and public irreversible communication.

SafeLoop requires explicit approval, strong evidence, rollback/compensation plan, and post-action verification.

## Primary roadmap

### 0.1.5 — Delta-first release alignment

Goal: reframe existing 0.1.x work around retrospective delta audit instead of approval UI.

Scope:

- Document the runtime-enforced approval lifecycle: `PENDING -> APPROVED -> IN_FLIGHT -> EXECUTED`, plus reject/revoke/expire paths.
- Document mode policy: Auto/Shadow, Guarded, Strict.
- Reframe the static dashboard as an **artifact/evidence viewer**, not the primary operator UX.
- Add a release packet explaining that SafeLoop is for delta audit, gap extraction, and rollback/resume packets after long agent runs.
- Keep HTTP and Telegram as optional transports only.

Expected artifacts:

- `docs/release-notes-0.1.5.md`
- updates to `docs/control-plane.md`, `docs/approval-lifecycle.md`, and examples if they drift from runtime enforcement
- updates to the 0.1.0 MVP spec positioning

Exit criteria:

- Docs use "delta-only", "agent action time-machine", "gap extraction", and "rollback/resume packet" consistently.
- Docs do not imply hosted dashboard security, Telegram-native approval, or production governance.

### 0.2.0 — Run timeline and delta packet MVP

Goal: produce a self-contained packet for a long-running agent run that answers: "what changed, what was skipped, what is risky, and where can we resume or roll back?"

Packet layout:

```text
run-delta-packet/
  manifest.json
  delta-brief.md
  gap-report.json
  timeline-summary.json
  decision-ledger.jsonl
  evidence-bundle.json
  policy-decisions.jsonl
  rollback-resume.md
  rollback-plan.json
  suggested-review-prompt.md
  approval-command.sh
```

Core requirements:

- `manifest.json` records schema version, packet id, run id, base checkpoint, head checkpoint, artifact paths, artifact SHA-256 digests, created-at timestamp, and expiry.
- `delta-brief.md` is concise and operator-first: changed files/artifacts, tests/checks, PR state, policy pauses, external side effects, unresolved findings.
- `gap-report.json` lists skipped tests, failed tests, stale TODOs, review comments not addressed, semantic duplicate work, missing rollback evidence, and unverifiable claims.
- `timeline-summary.json` compresses timeline events into phases: goal set, checkpoint, test, review, PR, merge, resume, external side effect, approval, rollback.
- `decision-ledger.jsonl` records why the agent continued, paused, merged, skipped, retried, or accepted risk.
- `rollback-resume.md` gives the next operator or agent a one-screen handoff: safe resume command, rollback target, compensation handoff, and residual risk.
- `suggested-review-prompt.md` asks a review agent to judge gaps only, not rerun the whole task or execute the risky action.

Initial CLI shape:

```bash
safeloop delta create <run_dir> --from <checkpoint_id> --to latest --output run-delta-packet/
safeloop delta brief run-delta-packet/
safeloop delta gaps run-delta-packet/ --format json
safeloop delta prompt --target hermes run-delta-packet/
safeloop delta prompt --target codex run-delta-packet/
safeloop delta prompt --target claude-code run-delta-packet/
safeloop delta import-verdict run-delta-packet/ --verdict verdict.json --principal tt
```

Focused tests:

- manifest includes every packet artifact and rejects tampering;
- delta brief suppresses all-green baselines but surfaces failures and skips;
- gap report catches failed tests, missing test evidence, unresolved review notes, and unverified claims;
- policy decisions distinguish Auto/Shadow, Guarded, and Strict;
- rollback/resume packet links to the right checkpoint and refuses missing rollback plan;
- malicious action names cannot inject instructions into the review prompt;
- viewer verdict cannot approve a guarded continuation;
- operator/admin verdict can approve through the signed lifecycle path;
- consumed approvals cannot be replayed.

### 0.2.1 — Gap extractor and semantic drift detector

Goal: make after-the-fact review useful by extracting only the things a busy operator needs to inspect.

Detectors:

- failed or skipped tests;
- test evidence older than latest code checkpoint;
- PR review comments still open or not referenced by a fixing commit;
- TODO/FIXME/new backlog items introduced during the run;
- repeated attempts that modify the same files without new passing evidence;
- semantic duplicate artifacts, e.g. multiple dashboards/reports claiming the same status with different counts;
- docs claiming a feature exists when no implementation/test artifact changed;
- external side effect records without idempotency key or compensation mode;
- approval boundaries crossed without lifecycle-backed `IN_FLIGHT -> EXECUTED` transition.

Output classes:

- `action_required`: blocks merge/release/resume;
- `review`: human or review-agent should inspect;
- `watch`: notable but not blocking;
- `baseline`: green/noise, suppressed from briefs by default.

### 0.2.2 — Agent-native operator packet v2

Goal: keep approval/reject flows agent-native, but make them a subset of the broader delta packet rather than the entire product.

Scope:

- Generate approval packets from the same timeline/evidence/delta primitives.
- Preserve strict verdict import: schema validation, approval-id match, expiry, manifest digest checks, principal RBAC, signed lifecycle write, and no direct registry mutation.
- Add target-specific prompt exporters for Hermes, Codex, Claude Code, and OpenClaw.
- Keep review agents in judge-only mode: they can approve/reject/report gaps, not execute actions.

Non-goal: invoking those agents automatically. Export first; orchestration comes later.

### 0.2.3 — PR/release train watcher

Goal: support workflows like "open 0.1.0 PR -> review/merge -> continue RBAC/HMAC/static dashboard slices" without losing auditability.

Scope:

- Track PR lifecycle events: opened, review requested, checks started, checks passed/failed, comments, mergeability, merged, base branch advanced.
- Bind PR state to checkpoints and decision-ledger entries.
- Emit delta packets after each PR merge and before starting the next slice.
- Detect branch-stack drift and stale verification after rebase.
- Summarize only blockers, fallback increases, semantic duplicates, pytest failures, and actionable review findings.

Candidate commands:

```bash
safeloop pr-train watch --repo . --base main --run-id <run_id>
safeloop pr-train delta --since-last-merge <run_id>
safeloop pr-train resume-prompt <run_id> --next-slice rbac-hmac-dashboard
```

### 0.3.0 — Review agent orchestration

Goal: allow SafeLoop to dispatch packet review to available local agents while preserving SafeLoop as the authority.

Scope:

- Run judge-only review prompts through Hermes/Codex/Claude Code/OpenClaw adapters.
- Store raw review outputs as evidence.
- Require deterministic import through verdict schema, RBAC, signature, and replay checks.
- Treat malformed, unavailable, or contradictory review outputs as `review` or `action_required`, never as green.

Non-goal: agents directly approving registry rows or executing guarded actions.

### 0.4.0 — Optional viewers and transports

Goal: add convenience surfaces after packet/delta/lifecycle primitives are stable.

Candidates:

- Static evidence viewer v2 for run timelines and gap reports.
- Localhost HTTP viewer that reuses packet/verdict/lifecycle paths.
- Telegram/ChatOps notification transport if token setup is acceptable.
- Scheduled delta briefs for overnight runs.

These must remain viewers/transports. They must not become separate approval authorities.

## Deprioritized / optional tracks

### Hosted dashboard / SaaS control plane

Not near-term. Requires separate threat modeling for hosted auth, sessions, CSRF, multi-tenancy, secret isolation, audit retention, billing boundaries, and remote transparency logs.

### Telegram-first approvals

Not core. Useful only as a notification/transport layer after delta packets and verdict import are stable. BotFather/token setup should not block the product.

### Always-pausing guardrails

Not the default direction. SafeLoop should pause only for Guarded/Strict boundaries; otherwise it should let useful local work continue and make later review cheap.

## Positioning

Use:

> SafeLoop is a local-first control layer and action time-machine for autonomous agents. It lets agents run, records tamper-evident timelines and evidence, emits delta-only review packets, extracts gaps and rollback/resume options, and enforces RBAC/signature/replay protection when actions cross guarded boundaries.

Avoid claiming:

- production-grade governance;
- tamper-proof transparency;
- hosted dashboard security;
- Telegram-native approval without BotFather/token setup;
- automatic safety for all agent actions;
- that review agents are approval authorities;
- that green baselines should be sent as notifications.
