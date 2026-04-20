# SafeLoop FAQ

## Is SafeLoop trying to solve all agent safety?

No. SafeLoop narrows scope to execution discipline around actions: explicit action description, effect classification, journaled lifecycle state, and clearer recovery semantics.

## Does SafeLoop guarantee rollback?

No. SafeLoop distinguishes between reversible, compensatable, and irreversible effects so the system can be honest about what recovery might mean. Compensation is a defined cleanup path, not time travel.

## Why not just retry failed agent actions?

Retries can duplicate side effects or hide partial completion. SafeLoop's journal/lifecycle model exists so operators can ask what already happened before deciding whether to resume, compensate, hand off, or fail truthfully.

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
- tests covering those contracts and their integration

Runtime, storage, hooks, API, and demo work are still being hardened.

## Does the repo include a real GitHub demo today?

Not on this branch yet. The end-to-end GitHub-style reference flow is still outstanding for the Task 9 branch, and any future example should be labeled clearly as local/reference-only unless the code truly provides live integration behavior.

## Why use file/local language so often in the docs?

Because the project should not imply hosted or production control-plane capabilities that the implementation does not yet support.

## What should a first-time reader take away?

SafeLoop is an honest attempt to reduce blast radius in agent action execution by making actions typed, effects explicit, and lifecycle state journaled. It is promising infrastructure, not a finished safety guarantee.

## What does `handed_off` mean?

`handed_off` is a pre-execution terminal state. It means an approval hook escalated the action to an operator boundary before the executor ran.

This is different from `failed`:
- `failed` means the run could not proceed automatically
- `handed_off` means SafeLoop intentionally stopped and left the next step to an operator

For the current MVP, SafeLoop does not auto-run compensation around `handed_off`, and it does not treat `handed_off` as a resumable paused execution state.
