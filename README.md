# SafeLoop

SafeLoop is an early-stage Python project for *transactional agent execution*: a small kernel for describing risky actions explicitly, classifying their side effects, and recording their lifecycle in a journal instead of hiding everything inside a free-form agent loop.

This branch is still MVP-stage, but it now includes the first integrated cut of the core runtime path: storage, hooks, runtime, and a local inspection API. The repository is still being hardened, and some surfaces—especially docs, comparison material, and an end-to-end demo—are still incomplete. This README is intentionally narrow about what exists today.

## Why this exists now

Agents are getting better at planning and tool use, but real-world actions still fail in messy ways:

- retries can duplicate side effects
- partial execution is hard to inspect after the fact
- approval checks are easy to bolt on too late
- "rollback" often means guesswork unless the action was designed for it
- interruptions can leave operators asking whether to rerun, resume, or clean up manually

SafeLoop exists to reduce that ambiguity.

The project does **not** claim to solve agent safety in general. It focuses on a narrower problem: making action execution more legible, more reviewable, and more recovery-aware.

## What SafeLoop is

Today, SafeLoop is best understood as a local OSS kernel with a few concrete primitives:

- `ActionEnvelope`: a typed record of *what* an action is trying to do
- `EffectClass`: an explicit label for the action's side-effect risk
- `JournalState` and transition validation: a lifecycle model for how a run moves through approval, execution, failure, compensation, and recovery-oriented states
- package exports and tests that lock in those contracts as a starting point for harder runtime work

These primitives are meant to support a runtime where actions are proposed, approved, executed, journaled, and—when possible—resumed or compensated in a principled way.

## What SafeLoop is not

SafeLoop is **not**:

- a distributed workflow engine
- a production-ready orchestration control plane
- a guarantee that every side effect can be undone
- a hosted service
- a mature integration platform for GitHub or other third-party systems
- a claim that audit logs alone make an agent safe

If you are looking for durable multi-node execution, strong operational guarantees, or a complete policy system, this repository is not there yet.

## Core framing: action, effect, journal, lifecycle, recovery

### 1. Action

An action should be explicit before it runs.

In SafeLoop, `ActionEnvelope` is the typed declaration of intent. It includes fields such as:

- action `name`
- `target`
- structured `args`
- `actor`
- `privileges`
- `idempotency_key`
- `effect`

The point is simple: a write should look like a write *before* execution, not only after something goes wrong.

### 2. Effect

`EffectClass` captures how dangerous or reversible an action is expected to be:

- `read_only`
- `reversible_write`
- `compensatable_write`
- `irreversible_write`

This is not magic. The label does not make an action safe by itself. It gives the runtime and operators a shared vocabulary for approvals, rollback expectations, and recovery behavior.

### 3. Journal

A journal is more useful than a pile of logs if it reflects real execution state.

The current repo already defines `JournalEntry` and a validated transition graph. The supported states are:

- `proposed`
- `approved`
- `executing`
- `applied`
- `compensating`
- `compensated`
- `failed`
- `resumable`
- `handed_off`

This is the core of SafeLoop's claim: not that failure disappears, but that the system should preserve a truthful record of where execution got to.

### 4. Lifecycle

The intended lifecycle is criticism-aware:

- approval only matters if side effects do **not** happen before approval
- compensation is **not** time travel; it is an explicit best-effort cleanup path
- resumability should avoid blind reruns, not encourage them
- terminal states should be honest about whether a run applied, compensated, failed, or was handed off

### 5. Recovery

Why bother with this framing?

Because the real operator question after a failure is rarely "did the function raise?" It is usually:

- what already happened?
- what state is the run in now?
- can I resume safely?
- do I need compensation?
- is this action irreversible and therefore a manual incident?

SafeLoop is trying to make those questions answerable from first-class runtime data instead of inference.

## Current MVP status

On `feat/task-9-hardening`, the repository is still earlier than the eventual runtime vision.

Implemented now:

- typed action envelope model
- effect class enum
- journal state enum
- journal transition validation
- file-backed local journal storage
- approval and compensation hook registries
- storage-backed runtime execution with approval, compensation, and resume behavior
- local inspection API/read model
- tests covering those contracts

Still incomplete on this branch:

- comparison/case-study docs are still being expanded
- the end-to-end demo/reference flow is not yet merged into this branch
- import/packaging ergonomics are aimed at local development first, not packaged distribution polish

That means this repo is currently strongest as an *integrated local MVP kernel with honest limits*, not yet as a finished transactional execution platform.

## Quickstart

### Install

```bash
python -m pip install -e .
```

### Run the test suite

```bash
pytest -q
```

### Minimal contract check

You can validate the current integrated branch surface directly from the repository root:

```bash
python -c "from safeloop.runtime import Runtime; print('ok')"
```

Expected result: prints `ok`.

You can also run the full suite:

```bash
pytest -q
```

### Demo status

There is **not yet** a trustworthy end-to-end demo in this branch's current state. When the runtime/API/demo work lands, this section should point to a real local-safe flow rather than a mocked story.

## Architecture pointers

Current module layout:

- `src/safeloop/types.py` — `ActionEnvelope`, `EffectClass`
- `src/safeloop/journal.py` — `JournalState`, `JournalEntry`, transition validation
- `src/safeloop/storage.py` — file-backed journal storage
- `src/safeloop/hooks.py` — approval and compensation hook registries
- `src/safeloop/runtime.py` — storage-backed runtime execution
- `src/safeloop/api.py` — local inspection API/read model
- `tests/` — contract and integration coverage for storage, hooks, runtime, API, journal, and smoke imports

## Explicit non-goals and MVP limits

For this branch and current MVP, SafeLoop does **not** promise:

- production durability guarantees
- concurrent/distributed scheduling
- database-backed storage
- complete approval policy modeling
- automatic reversibility of all writes
- connector maturity for external services
- a stable operator UI
- proof that an agent system is "safe" in a broad sense

The project is better described as "early infrastructure for bounded, inspectable agent actions" than as a finished safety product.

## Supporting docs

- `docs/state-machine-and-journal-schema.md` — current state machine, transition rules, and journal schema summary
- `docs/faq.md` — concise criticism-aware FAQ for first-time readers
- `docs/plans/2026-04-18-task-9-hardening-plan.md` — Task 9 plan for technical and positioning hardening
- `docs/notes/task-9-positioning-hardening-outline.md` — messaging guardrails used to shape this README

## Development note

This README intentionally avoids words like "guaranteed," "fully safe," or "production-grade." Those claims should only appear when the runtime, storage, tests, and demo prove them.
