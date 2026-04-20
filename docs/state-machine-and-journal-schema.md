# State Machine and Journal Schema

This document summarizes SafeLoop's current runtime lifecycle for contributors and reviewers.

It is intentionally narrow: it mirrors the behavior implemented on `main` today, not a future distributed system. The code in `src/safeloop/journal.py`, `src/safeloop/runtime.py`, and `src/safeloop/api.py` remains the source of truth if this document drifts.

## Journal state enum

The current runtime defines these journal states in `src/safeloop/journal.py`:

- `proposed`
- `approved`
- `executing`
- `applied`
- `compensating`
- `compensated`
- `failed`
- `resumable`
- `handed_off`

These states are persisted as `JournalEntry.state` values and are the source of truth for both runtime history and the inspection API.

## Transition table

Allowed transitions are currently:

| From | Allowed next states |
| --- | --- |
| `proposed` | `approved`, `failed` |
| `approved` | `executing`, `handed_off` |
| `executing` | `applied`, `compensating`, `failed`, `resumable`, `handed_off` |
| `compensating` | `compensated`, `failed` |
| `resumable` | `executing` |
| `applied` | _(terminal)_ |
| `compensated` | _(terminal)_ |
| `failed` | _(terminal)_ |
| `handed_off` | _(terminal)_ |

Any other transition is rejected by `validate_transition()`.

## State meaning

### `proposed`
A run record exists, but execution has not yet been approved or started.

### `approved`
The runtime accepted the action for execution. This is still pre-execution state.

### `executing`
The executor is running or resuming. This is the primary execution phase where side effects may actively be occurring.

### `applied`
Execution finished successfully and the runtime recorded a successful terminal state.

### `compensating`
The runtime encountered an execution error on a compensatable action and is attempting cleanup.

### `compensated`
Cleanup completed successfully and the runtime recorded that cleanup path explicitly.

### `failed`
Execution failed and the runtime ended in a terminal failure state.

In the current implementation, `failed` can arise from:
- approval hook exceptions
- approval block
- execution exceptions without compensation
- compensation attempts that themselves fail

### `resumable`
The executor raised `ResumableExecution`, leaving a checkpoint in the live runtime instance so a later call can continue.

### `handed_off`
The runtime stopped and delegated control instead of continuing autonomously.

Today this is emitted when approval escalates before execution (`approved -> handed_off`).

The transition table also allows `executing -> handed_off`, but the current `Runtime.run()` implementation shown on `main` does not emit that execution-time path yet.

## Journal schema

The persisted journal record is intentionally small.

`JournalEntry` currently contains:

| Field | Type | Meaning |
| --- | --- | --- |
| `run_id` | `str` | Stable identifier for the runtime attempt/history stream |
| `action_id` | `str` | The action's idempotency key bound to the run |
| `state` | `JournalState` | The lifecycle state recorded for that step |

The journal is append-only from the runtime's perspective: each new state transition appends a new `JournalEntry`.

## Runtime-to-viewer/API mapping

The current inspection surfaces do not add a parallel state vocabulary.

### `RunRecord`
`Runtime.get_run()` returns a runtime object with:
- `run_id`
- `action_id`
- latest `state`
- full `journal`

### `RunSummary`
The inspection API/viewer summary intentionally exposes only:
- `run_id`
- `action_id`
- `state`

### `RunDetail`
The detailed inspection API/viewer adds:
- `journal` as serialized entries

There is currently a direct mapping:
- latest runtime journal state -> viewer/API `state`
- full runtime journal -> viewer/API `journal` on the detail endpoint

## Current limits

This document is about the current implementation only.

It does not claim:
- timestamp metadata in `JournalEntry`
- persisted checkpoint payloads in the journal
- a separate viewer/API state taxonomy beyond the journal state string
- distributed recovery semantics

## Related files

- `src/safeloop/journal.py`
- `src/safeloop/runtime.py`
- `src/safeloop/api.py`
- `tests/test_journal.py`
- `tests/test_runtime.py`
- `tests/test_api.py`
