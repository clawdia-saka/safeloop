# SafeLoop comparisons

## What SafeLoop is trying to be

SafeLoop is a **recovery-first execution safety layer** for side-effecting agent actions.
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

## SafeLoop vs “just add guardrails”

Guardrails help before or around execution.

SafeLoop is strongest at execution-state truth after something starts happening:

- journaling
- terminal state clarity
- compensation tracking
- resume checkpoints

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
- perfect rollback
- broad connector coverage

The strongest current claim is narrower:

> SafeLoop makes agent-side effects more legible, more recoverable, and easier to reason about than an unstructured tool loop.
