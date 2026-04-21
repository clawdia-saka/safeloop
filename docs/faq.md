# SafeLoop FAQ

## Is SafeLoop trying to solve all agent safety?

No. SafeLoop narrows scope to execution discipline around actions: explicit action description, effect classification, journaled lifecycle state, and clearer recovery semantics.

## Does SafeLoop guarantee rollback?

No. SafeLoop distinguishes between reversible, compensatable, and irreversible effects so the system can be honest about what recovery might mean. Compensation is a defined cleanup path, not time travel.

The easiest way to read the current semantics correctly is:

| Term | SafeLoop meaning | Misread to avoid |
| --- | --- | --- |
| `reversible_write` | The write is expected to have a genuine reverse operation. | Do not assume every write with cleanup support is reversible. |
| `compensatable_write` | The write has a compensation hook SafeLoop may call after execution failure. | Do not read this as “rollback guaranteed.” |
| `compensated` | The compensation hook completed successfully. | Do not read this as “the world is back to exactly pre-run state.” |
| `compensation_failed` | SafeLoop attempted compensation, but the hook raised/failed. | Do not collapse this into generic `failed` or hidden rollback success. |

For compensatable actions, the runtime now makes a further distinction:
- `compensated` means cleanup completed successfully after an execution failure
- `compensation_failed` means cleanup was attempted but the compensation hook itself failed

SafeLoop may also expose `reason` and `error` metadata in journal/API output. In the current local MVP, `reason` is a small machine-readable category, while `error` is local diagnostic exception text rather than a hardened, sanitized public contract.

The viewer/API now also adds two derived interpretation fields:
- `scope`: `inside_mvp_scope` or `boundary_case`
- `boundaries`: a short list of tags derived from `state` and `reason`

Read them as helper labels, not as a replacement for runtime truth. `state` and `reason` still carry the canonical semantics; `scope` and `boundaries` just make first-pass interpretation less error-prone.

So even when SafeLoop tries to recover, it does **not** claim that external side effects were perfectly rolled back. In SafeLoop docs, any use of “rollback” should be read as shorthand only when the write is actually reversible; compensatable writes get cleanup semantics, not an “as-if-never-happened” guarantee.

## Why not just retry failed agent actions?

Retries can duplicate side effects or hide partial completion. SafeLoop's journal/lifecycle model exists so operators can ask what already happened before deciding whether to resume, compensate, hand off, or fail truthfully.

`handed_off` is intentionally different from `failed`: it means approval escalated before execution began, so the runtime stopped automatic execution and expects operator or external follow-up instead of claiming the action already ran.

For concrete runnable illustrations, see:
- `examples/boundary_demos.py` for `handed_off`, `compensation_failed`, `ambiguous_side_effect`, `resumable`, `repeated_resume`, and the docs-only `unsupported_rollback_expectation` reference
- `docs/case-studies/boundary-scenarios.md` for the current `in_scope` / `boundary` / `unsupported` example matrix

For resumable runs, the detail/viewer surface may expose `has_checkpoint=true`. In the current local MVP, that only means the live runtime instance still holds checkpoint data for resume; it is not a promise of durable persisted checkpoint storage. If a persisted run is still marked `resumable` but the current runtime has no live checkpoint, SafeLoop will not blindly resume with `checkpoint=None`.

## Is this a workflow engine?

Not today. The repository is much closer to a local transactional execution kernel than to a full distributed orchestration platform.

## What is implemented right now?

On this branch, the strongest concrete pieces are:

- `ActionEnvelope`
- `EffectClass`
- `JournalState`
- `JournalEntry`
- file-backed local journal storage
- approval and compensation hook registries
- a storage-backed runtime with approval, compensation, handoff, and resumable flows
- a local inspection API/read model
- a local GitHub-style reference demo that runs through the real runtime and persists inspectable journal history
- tests covering those contracts and their integration

Runtime, storage, hooks, API, and demo work are still being hardened.

## Does the repo include a real GitHub demo today?

Yes, but narrowly. The repo now includes a **local GitHub-style reference demo** that executes through the real runtime, persists journal state, and can be inspected through the viewer/API surfaces.

What it is **not**:
- a live GitHub integration
- a production connector
- proof of durable distributed recovery

The demo should still be read as **local/reference-only**, even though it is now runtime-backed rather than a parallel mocked state machine.

## Why use file/local language so often in the docs?

Because the project should not imply hosted or production control-plane capabilities that the implementation does not yet support.

## What should a first-time reader take away?

SafeLoop is an honest attempt to reduce blast radius in agent action execution by making actions typed, effects explicit, and lifecycle state journaled. It is promising infrastructure, not a finished safety guarantee.