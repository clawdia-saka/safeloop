# SafeLoop

SafeLoop is an early-stage Python project for *transactional agent execution*: a small kernel for describing risky actions explicitly, classifying their side effects, and recording their lifecycle in a journal instead of hiding everything inside a free-form agent loop.

This branch is still MVP-stage, but it now includes the first integrated cut of the core runtime path: storage, hooks, runtime, a local inspection API, and a runtime-backed local reference demo. The repository is still being hardened, and some surfaces—especially comparison material and public-facing docs—remain incomplete. This README is intentionally narrow about what exists today.

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

For the canonical current-state contract, see [state machine and journal schema](docs/specs/state-machine-and-journal-schema.md).

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

This is not magic. The label does not make an action safe by itself. It gives the runtime and operators a shared vocabulary for approvals, *qualified* rollback expectations, and recovery behavior.

In particular, SafeLoop draws a hard line between **reversal** and **compensation**:

| Effect class / state | What it means | What it does **not** mean |
| --- | --- | --- |
| `reversible_write` | The action is expected to have a real reverse path, so operators can reasonably talk about undoing the write itself. | It does not guarantee that every external observer saw no trace or that reversal is always available in practice. |
| `compensatable_write` | The action has a defined cleanup path if execution fails after side effects begin. | It is **not** the same thing as rollback; compensation is best-effort cleanup, not a promise that the original write is undone as if it never happened. |
| `compensated` | A compensation hook ran successfully after an execution failure. | It does not certify full rollback of external effects. It only says the defined compensation path completed. |
| `compensation_failed` | SafeLoop attempted compensation, but the compensation hook itself failed. | It is not interchangeable with generic `failed`, and it definitely does not imply a hidden rollback success. |

If a reader casually substitutes “rollback” everywhere they see “compensation,” they will overread the current implementation.

### 3. Journal

A journal is more useful than a pile of logs if it reflects real execution state.

The current repo already defines `JournalEntry` and a validated transition graph. The supported states are:

- `proposed`
- `approved`
- `executing`
- `applied`
- `compensating`
- `compensated`
- `compensation_failed`
- `failed`
- `resumable`
- `handed_off`

Three distinctions matter in the MVP:

- `failed` means the automatic path terminated without execution succeeding, whether that happened before execution (`approval_block`, `approval_error`) or after execution began without a successful compensation outcome
- `compensation_failed` means SafeLoop *did* attempt compensation for a `compensatable_write`, but the compensation hook itself failed, so cleanup is incomplete or uncertain
- `handed_off` means SafeLoop approved the action path but escalated before execution, transferring the next step to an operator or outside system without starting side effects

Journal entries may also include `reason` and `error` metadata. In the current local MVP, `error` is best read as **diagnostic text for local inspection**, not as a stable public error taxonomy or a sanitized production-safe field.

Run summary/detail and journal payloads now also expose additive interpretation fields:
- `scope`: `inside_mvp_scope` or `boundary_case`
- `boundaries`: small derived tags such as `pre_execution`, `operator_owned`, `checkpoint_recorded`, `cleanup_attempted`, `cleanup_incomplete_or_uncertain`, `side_effects_possible`, and `terminal`

These fields are derived from canonical runtime facts (`state` + `reason`). They are there to make the current MVP easier to read; they are **not** a second state machine and they do not replace the canonical journal truth.

For resumable runs, run detail output also exposes `has_checkpoint` as a local inspection hint. It only indicates whether the current runtime instance still holds resumable checkpoint state; it does **not** expose raw checkpoint contents and should not be read as durable cross-process persistence. If a journal says `resumable` but a fresh runtime instance has no live checkpoint payload, SafeLoop now treats the run as non-resumable-in-practice and will not blindly re-execute it.

This is the core of SafeLoop's claim: not that failure disappears, but that the system should preserve a truthful record of where execution got to.

### 4. Lifecycle

The intended lifecycle is criticism-aware:

- approval only matters if side effects do **not** happen before approval
- `reversible_write` and `compensatable_write` are different promises; only the former should suggest a true reverse operation
- compensation is **not** time travel and not a synonym for rollback; it is an explicit best-effort cleanup path
- `compensated` means the cleanup hook finished, while `compensation_failed` means SafeLoop tried cleanup and could not complete it
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

The repository is still earlier than the eventual runtime vision.

Implemented now:

- typed action envelope model
- effect class enum
- journal state enum
- journal transition validation
- file-backed local journal storage
- approval and compensation hook registries
- storage-backed runtime execution with approval, compensation, handoff, and resume behavior
- local inspection API/read model
- a local GitHub-style reference demo that executes through the real runtime and persists journal history for viewer/API inspection
- runnable boundary demos for handoff, compensation failure, and resumable execution
- tests covering those contracts

Still incomplete on this branch:

- comparison/case-study docs are still being expanded
- the demo is local/reference-only rather than a live GitHub integration
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

There **is** now a local GitHub-style reference demo in `examples/github_pr_demo.py`, plus dedicated boundary scenario demos in `examples/boundary_demos.py`.

What they prove today:
- the demos execute through the real `Runtime`
- journal history is persisted to local storage
- the resulting runs can be inspected through `RunViewer` and the HTTP API
- success, compensation, handoff, compensation-failure, and resumable paths all share the same runtime-owned truth
- docs now classify examples as `in_scope`, `boundary`, or `unsupported` in [`docs/case-studies/boundary-scenarios.md`](docs/case-studies/boundary-scenarios.md)

What they do **not** prove:
- live GitHub integration
- distributed durability
- production-grade incident handling
- perfect rollback of external side effects

So the right description is: **runtime-backed local reference demos, not live integration demos**.

## Architecture pointers

Current module layout:

- `src/safeloop/types.py` — `ActionEnvelope`, `EffectClass`
- `src/safeloop/journal.py` — `JournalState`, `JournalEntry`, transition validation
- `src/safeloop/storage.py` — file-backed journal storage
- `src/safeloop/hooks.py` — approval and compensation hook registries
- `src/safeloop/runtime.py` — storage-backed runtime execution
- `src/safeloop/api.py` — local inspection API/read model
- `examples/github_pr_demo.py` — runtime-backed local GitHub-style reference demo
- `examples/boundary_demos.py` — runtime-backed handoff / compensation-failure / resumable boundary examples
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

- [state machine and journal schema](docs/specs/state-machine-and-journal-schema.md) — canonical current SafeLoop state machine and journal schema
- [boundary scenarios](docs/case-studies/boundary-scenarios.md) — example matrix for in-scope, boundary, and unsupported interpretations
- `docs/faq.md` — concise criticism-aware FAQ for first-time readers
- `docs/plans/2026-04-18-task-9-hardening-plan.md` — Task 9 plan for technical and positioning hardening
- `docs/notes/task-9-positioning-hardening-outline.md` — messaging guardrails used to shape this README

## Development note

This README intentionally avoids words like "guaranteed," "fully safe," or "production-grade." Those claims should only appear when the runtime, storage, tests, and demo prove them.
