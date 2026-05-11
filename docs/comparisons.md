# SafeLoop comparisons

## What SafeLoop is trying to be

SafeLoop is a **local execution kernel for explicit recovery semantics** around side-effecting agent actions.
It is not trying to replace every workflow or orchestration tool. Its narrow goal is to make risky actions:

- typed before execution
- effect-aware
- journaled through lifecycle transitions
- approval-aware
- compensation-aware
- resumable when appropriate

## SafeLoop vs workflow / orchestration engines

Workflow engines help coordinate *what should happen next*.

SafeLoop is focused on *what happened when an action with side effects partially failed*.

That means SafeLoop cares most about:

- action identity
- effect classification
- journal truth
- approval decisions
- compensation paths
- recovery semantics

A workflow engine can still be useful around SafeLoop.

## SafeLoop vs ad hoc retries

Ad hoc retries are cheap until a side effect lands halfway.

SafeLoop adds structure around that moment:

- was this action read-only, reversible, compensatable, or irreversible?
- did it get approved?
- did it execute?
- should it resume, compensate, fail, or hand off?

Retries alone do not answer those questions.

## Recovery terms that should not be conflated

SafeLoop intentionally uses more than one recovery term because they are *not* interchangeable:

| Term | Narrow meaning in the current MVP |
| --- | --- |
| `reversible_write` | The action is expected to support a genuine reverse operation. This is the closest SafeLoop gets to a real rollback-style claim. |
| `compensatable_write` | The action has a defined compensation hook for cleanup after execution failure. This is weaker than reversal. |
| `compensated` | The compensation hook completed. It records cleanup success, not proof that every side effect was erased. |
| `compensation_failed` | Cleanup was attempted and failed. This is a more specific and more alarming state than generic `failed`. |

If you want a one-line rule: **reversible** suggests “undo the write,” while **compensatable** suggests “run the defined cleanup path and record honestly how that went.”

## SafeLoop vs “just add guardrails”

Guardrails help before or around execution.

SafeLoop is strongest at execution-state truth after something starts happening:

- journaling
- terminal state clarity
- compensation tracking
- resume checkpoints persisted in the local journal for same-action resume across runtime instances

It does not make model output safe by itself.

## SafeLoop vs durable saga platforms

Durable saga platforms can offer stronger operational guarantees than the current SafeLoop MVP.

SafeLoop's current value is not “we out-durable them.”
Its value is that it provides a smaller, more local kernel for teams that want explicit recovery semantics around agent actions without starting from a full orchestration platform.

## Current honest limits

Today’s MVP is still local and early-stage.

It does **not** claim:

- distributed durability
- production control-plane maturity
- full policy UI / enterprise RBAC
- unqualified rollback guarantees
- broad connector coverage

The strongest current claim is narrower:

> SafeLoop makes agent-side effects more legible, easier to inspect, and easier to reason about in recovery terms than an unstructured tool loop.
