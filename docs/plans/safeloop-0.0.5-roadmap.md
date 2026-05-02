# SafeLoop 0.0.5 Roadmap: Side-Effect Ledger and Adapter Prep

Status: planning-only branch for parallel 0.0.5 preparation.  
Branch intent: docs/spec only; do not modify runtime, CLI, package version, or tests while 0.0.4 watchdog reliability is active.

## Recommended scope

SafeLoop 0.0.5 should focus on **external side-effect accounting**:

1. Promote `side-effects.jsonl` from placeholder to documented ledger.
2. Define private-first adapter boundaries for effectful systems.
3. Add inspectable, redacted records for intent/prepare/commit/compensation phases.
4. Keep all writes local by default.
5. Defer hosted control-plane work until after the ledger contract is stable.

This is the natural next step after watchdog reliability because the watchdog can prove local timeline integrity, but operators still need a trustworthy account of effects that escape the repo/process boundary.

## Work packages

### 0.5-A — Schema freeze

- Finalize `side-effect-ledger.v1` JSONL fields.
- Decide allowed `phase`, `effect_class`, and `compensation.capability` enums.
- Document redaction invariants and example records.

### 0.5-B — Local ledger writer seam

- Add an append-only writer that receives already-redacted records.
- Keep ordering compatible with 0.0.4 artifact durability decisions.
- Do not change watchdog supervision behavior.

### 0.5-C — Adapter contract skeleton

- Define adapter identity/version metadata.
- Define prepare/commit/compensate result shapes.
- Require idempotency keys where target systems support them.
- Keep real external writes behind explicit opt-in examples or mocks.

### 0.5-D — Inspection/read-model extension

- Summarize side-effect counts by phase/effect class.
- Surface compensation status and external refs when present.
- Redact by default in CLI/API output.

### 0.5-E — Future contract tests

Add tests only once implementation begins, keeping them isolated from 0.0.4 reliability assertions:

- strict redaction of persisted records
- prepared vs committed distinction
- adapter identity and idempotency requirements
- explicit compensation capability
- unknown side-effect representation
- run/artifact binding

## Collision-avoidance rules for implementation

- Do not edit 0.0.4-owned watchdog supervision code until 0.0.4 is merged or its branch points are known stable.
- Do not update `pyproject.toml` version on the planning branch.
- Do not add enabled tests that fail against 0.0.3/0.0.4 runtime behavior.
- Prefer new files/modules for ledger implementation after the spec is accepted.
- Treat `side-effects.jsonl` as an additive artifact contract layered on top of the watchdog packet.

## Deferred to 0.0.6+

- Remote transparency log.
- Multi-user control plane/RBAC.
- Hosted dashboard.
- Real SaaS adapters enabled by default.
- Cryptographic signing beyond local hash-chain/timeline binding.
