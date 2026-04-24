# Issue 17: global semantic duplicate guard

## Decision
SafeLoop should expose a small, deterministic semantic duplicate guard that can be used by long-running scenario sweepers to remember all scenarios seen in a run, not only a short recent window.

## Semantics
- A scenario fingerprint is derived from normalized `kind`, `effect`, `failure_mode`, `goal`, and `why` fields.
- Fingerprints are stable for equivalent whitespace/case/punctuation variants.
- Names are intentionally excluded so renamed replays of the same semantic motif are still duplicates.
- The guard tracks a run-wide fingerprint ledger and reports whether each newly observed scenario is novel.
- The default ledger is intentionally unbounded for the lifetime of one guard/run so early-run fingerprints are not forgotten later in the same run. This is per-run state owned by the caller, not process-global daemon memory.
- `max_fingerprints` is an optional active FIFO bound for callers with strict memory budgets. When callers choose a bounded active ledger, they should not expect bounded memory to also provide true all-time duplicate detection.
- This PR exposes the reusable guard in the main package; wiring it into the non-git overnight-lite sweeper is intentionally outside this repository change.

## Acceptance criteria
- Add a focused utility in the runtime package that computes semantic fingerprints without external dependencies.
- Add tests proving renamed/reworded duplicates are rejected across a long stream, while genuinely distinct scenarios remain novel.
- Keep this additive; do not alter journal/runtime state transitions.
