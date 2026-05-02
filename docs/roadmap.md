# SafeLoop roadmap: agent-native operator plane

Status: direction update after 0.1.x control-plane/runtime-enforcement work.

SafeLoop's next product direction is **agent-native by default**: emit self-contained review and approval packets that OpenClaw, Hermes, Codex, Claude Code, or a human operator can inspect directly. HTTP dashboards and Telegram bots are optional transports/viewers, not the core control plane.

## Product thesis

SafeLoop should not require a hosted UI, browser session, or BotFather-managed bot to be useful. The core UX should be:

1. an agent attempts a risky action;
2. SafeLoop stops at an approval boundary;
3. SafeLoop emits a local, tamper-evident operator packet;
4. a human or review agent judges the packet and returns a structured verdict;
5. SafeLoop imports the verdict, enforces RBAC/signature/replay checks, and only then lets the runtime consume the approval.

This keeps SafeLoop local-first, CLI-friendly, agent-readable, and easy to plug into existing operator workflows.

## Primary roadmap

### 0.1.5 — CLI-first release alignment

Goal: align docs and examples around the already-merged runtime-enforced approval model.

Scope:

- Document the actual lifecycle contract: `PENDING -> APPROVED -> IN_FLIGHT -> EXECUTED`, plus reject/revoke/expire paths where present.
- State that write-effect runtime execution requires a lifecycle-backed approval store, not a lookup-only record.
- State that resume must revalidate the `IN_FLIGHT` approval against action, subject, expiry, status, HMAC/signature, and stored equality before executor re-entry.
- Reframe the static dashboard as a **static artifact viewer**, not the future primary operator UX.
- Keep Telegram and HTTP listed as optional later transports, not roadmap blockers.
- Add a release packet describing security properties, non-goals, and known limits without production overclaiming.

Expected artifacts:

- `docs/release-notes-0.1.5.md`
- updates to `docs/control-plane.md`, `docs/approval-lifecycle.md`, and examples if they drift from runtime enforcement
- tests only if documentation examples execute code

### 0.2.0 — Agent-native operator packet MVP

Goal: SafeLoop can produce a self-contained review packet for pending approvals and import a structured verdict without needing HTTP, Telegram, or an external identity provider.

Packet layout:

```text
operator-packet/
  manifest.json
  review-packet.md
  suggested-agent-prompt.md
  approval-request.json
  evidence-bundle.json
  policy-decision.json
  rollback-plan.json
  timeline.jsonl
  approval-command.sh
```

Core requirements:

- `manifest.json` records schema version, packet id, approval id, run id, artifact paths, artifact SHA-256 digests, created-at timestamp, and expiry.
- `review-packet.md` is human-readable and safe to paste into OpenClaw/Hermes/Codex/Claude Code.
- `suggested-agent-prompt.md` asks the review agent to **judge only** and not execute the action.
- JSON artifacts are machine-readable and canonical enough for deterministic hashing.
- Dynamic Markdown fields are fenced, quoted, or escaped so malicious action names/subjects cannot rewrite instructions inside the packet.
- The packet clearly separates facts, reviewer criteria, and output schema.

Initial CLI shape:

```bash
safeloop packet create <run_dir> --approval-id <approval_id> --output operator-packet/
safeloop packet show operator-packet/
safeloop packet prompt --target hermes operator-packet/
safeloop packet prompt --target claude-code operator-packet/
safeloop packet prompt --target codex operator-packet/
safeloop packet prompt --target openclaw operator-packet/
safeloop packet import-verdict operator-packet/ --verdict verdict.json --principal tt
```

Verdict import rules:

- Accept only a strict JSON verdict schema.
- Reject malformed JSON, unknown decisions, wrong approval id, expired packets, missing manifest entries, artifact digest mismatch, and packet tampering.
- Reject approve verdicts from principals lacking `approve` permission.
- Write the approval/rejection through the existing lifecycle store and signing path; do not mutate registry rows directly.
- Preserve runtime replay protection: approved records are still consumable and must transition through `IN_FLIGHT` before execution.

Focused tests:

- packet manifest includes all expected artifacts and hashes;
- packet tamper is rejected on import;
- prompt injection text in action/subject stays data, not instructions;
- malformed/wrong-id verdicts fail closed;
- viewer verdict cannot approve;
- operator/admin verdict can approve;
- reject verdict writes a rejected lifecycle event;
- approved verdict cannot be replayed after runtime consumption.

### 0.2.1 — Agent prompt exporters

Goal: provide target-specific review prompt wrappers without integrating directly with each agent runtime.

Targets:

- Hermes: concise task packet with file list and required final JSON.
- Claude Code: repo/worktree path, read-only review instruction, no file modification.
- Codex: strict JSON output and no execution side effects.
- OpenClaw: skill-style packet with decision criteria and output contract.

Non-goal: invoking those agents automatically. Export first; orchestration can come later.

### 0.2.2 — Operator inbox CLI

Goal: make local operation pleasant without adding a web surface.

Candidate commands:

```bash
safeloop approvals next --format packet
safeloop approvals list --status pending
safeloop approvals packet <approval_id>
safeloop approvals apply-verdict <approval_id> --verdict verdict.json
safeloop approvals timeline <approval_id>
safeloop evidence show <run_id>
```

This should be the default operator experience before any live dashboard work.

## Deprioritized / optional tracks

### Local HTTP operator plane

Keep as optional after the agent-native packet is working. If added, it should be localhost-only by default, explicitly expose risk when binding public interfaces, and reuse the same packet/verdict/lifecycle code paths. It must not become a separate approval authority.

### Telegram ChatOps

Useful only if a BotFather/token setup is acceptable. Until then, Telegram should be treated as a future notification/transport layer. Do not block the core product on Telegram identity binding, callback-query replay handling, or Bot API token management.

### Hosted dashboard / SaaS control plane

Not part of the near-term roadmap. It would require separate threat modeling for hosted auth, sessions, CSRF, multi-tenancy, secret isolation, audit retention, and remote transparency logs.

## Positioning

Use:

> SafeLoop is a local-first control layer for autonomous agent actions. It emits agent-native approval packets that humans or review agents can inspect, while SafeLoop enforces permissions, signatures, replay protection, artifact hashes, and runtime gating.

Avoid claiming:

- production-grade governance;
- tamper-proof transparency;
- hosted dashboard security;
- Telegram-native approval without BotFather/token setup;
- automatic safety for all agent actions.
