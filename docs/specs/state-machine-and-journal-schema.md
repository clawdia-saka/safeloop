# SafeLoop Canonical State Machine and Journal Schema

This document is the canonical reference for the SafeLoop state machine and persisted journal schema as they exist in the current codebase.

Truth sources:
- `src/safeloop/journal.py`
- `src/safeloop/runtime.py`
- `src/safeloop/api.py`
- `src/safeloop/storage.py`
- `tests/test_journal.py`
- `tests/test_runtime.py`
- `tests/test_api.py`

This spec is intentionally descriptive, not aspirational. If the code changes, this document should change with it.

## 1. Journal entry schema

SafeLoop persists one JSON object per line in a JSONL file.

Current schema:

```json
{
  "run_id": "string",
  "action_id": "string",
  "state": "proposed|approved|executing|applied|compensating|compensated|compensation_failed|failed|resumable|handed_off",
  "reason": "approval_error|approval_block|approval_completion_error|resume_approval_block|handoff_requested|execution_error|compensation_error|null",
  "error": "string|null"
}
```

Viewer/API payloads add derived interpretation fields on top of those journal facts:
- `scope`: `inside_mvp_scope|boundary_case`
- `boundaries`: list of short tags derived from `(state, reason)`
- `terminal_semantics`: a structured `TerminalSemantics` payload with `terminal_state`, `expected_terminal_state`, `boundary`, `scope_guess`, and `note`

These additive fields are **not persisted** in journal storage. They are computed in the API/viewer layer so the canonical journal schema stays small and runtime-owned.

Field meanings:
- `run_id`: stable run identity used to group entries in storage and API output.
- `action_id`: the `ActionEnvelope.idempotency_key` bound to that run.
- `state`: the lifecycle state appended for this step.
- `reason`: optional machine-readable cause for states that need more context. Known current values are the `JournalReason` enum values, but storage/viewer reads now tolerate unknown legacy strings so older journals can still be inspected.
- `error`: optional local diagnostic text, usually `str(exception)`.

Notes:
- `run_id`, `action_id`, and `state` are required by the `JournalEntry` model.
- `reason` and `error` are optional and omitted from API output when `None`.
- storage validates every JSONL line by loading JSON and then validating it against `JournalEntry`.
- malformed or schema-invalid lines raise `JournalStorageError` during reads.

## 2. Canonical states

`JournalState` currently defines exactly these values:

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

### Non-terminal states

These states may transition to another state:

- `proposed`
- `approved`
- `executing`
- `compensating`
- `resumable`

### Terminal states

These states are terminal in the current runtime and transition map:

- `applied`
- `compensated`
- `compensation_failed`
- `failed`
- `handed_off`

Runtime behavior for terminal states:
- repeated `Runtime.run()` calls with the same `run_id` and same `action_id` return the latest journal entry unchanged
- the executor is not called again
- no new journal entries are appended

## 3. Allowed transitions

The transition graph enforced by `validate_transition()` is:

- `proposed -> approved`
- `proposed -> failed`
- `approved -> executing`
- `approved -> handed_off`
- `executing -> applied`
- `executing -> compensating`
- `executing -> failed`
- `executing -> resumable`
- `compensating -> compensated`
- `compensating -> compensation_failed`
- `resumable -> executing`
- `resumable -> failed`

Everything else is invalid, including:
- `executing -> handed_off`
- any transition out of `applied`
- any transition out of `compensated`
- any transition out of `compensation_failed`
- any transition out of `failed`
- any transition out of `handed_off`

## 4. Runtime meaning of each state

### `proposed`
The runtime has accepted a new `run_id` and appended the first journal entry before approval or execution.

### `approved`
The action has passed the runtime's current approval path.

Important caveat: read-only actions also pass through `approved`. The runtime bypasses approval hooks for `EffectClass.READ_ONLY`, but it still appends `approved` before execution.

### `executing`
The runtime is about to call, or has just re-entered, the executor.

This state is used for both first execution and resumed execution.

### `applied`
The executor returned successfully. This is terminal.

### `compensating`
The executor raised an exception for a `COMPENSATABLE_WRITE`, so the runtime recorded the execution failure and is attempting compensation.

Current metadata expectation:
- `reason=execution_error`
- `error` contains the original executor exception text

### `compensated`
Compensation completed successfully after an execution failure. This is terminal.

Compensation is not rollback: successful compensation does not prove perfect rollback. It means the configured compensation hook completed and the operator should still treat the result as mitigation rather than proof of perfect rollback.

### `compensation_failed`
Compensation was attempted but the compensation hook raised its own error. This is terminal.

`compensation_failed` is not rollback. It means cleanup remains incomplete or uncertain after a compensation attempt, and viewers/API payloads must avoid wording that implies exact reversal.

Current metadata expectation:
- `reason=compensation_error`
- `error` contains the compensation hook exception text

### `failed`
The automatic runtime path terminated unsuccessfully without ending in successful compensation.

Current code uses `failed` for four cases:
- approval hook raised: `reason=approval_error`
- approval hook blocked execution before executor entry: `reason=approval_block`
- approval completion failed after executor success: `reason=approval_completion_error`
- resume approval failed after a live checkpoint: `reason=resume_approval_block`
- executor raised for a non-compensatable path: `reason=execution_error`

This state is terminal.

### `resumable`
Execution paused by raising `ResumableExecution(checkpoint)`. The runtime stores the checkpoint in the current runtime instance and records `resumable` in the journal.

This state is non-terminal. A later `run()` with the same `run_id` and `action_id` may append `executing` and continue from the stored checkpoint.

### `handed_off`
Approval completed with `ApprovalDecision.ESCALATE`, so the runtime stopped automatic execution before calling the executor. This is terminal.

Current metadata expectation:
- `reason=handoff_requested`
- `error` omitted

## 5. Runtime path mapping

### Success path
Typical path:
- `proposed -> approved -> executing -> applied`

### Approval block
Path:
- `proposed -> failed`

Metadata:
- `reason=approval_block`

The executor is not called.

### Approval hook error
Path:
- `proposed -> failed`

Metadata:
- `reason=approval_error`
- `error=str(exception)`

The executor is not called.

### Handoff / escalation
Path:
- `proposed -> approved -> handed_off`

Metadata:
- `reason=handoff_requested`

The executor is not called and no checkpoint is created.

### Non-compensatable execution failure
Path:
- `proposed -> approved -> executing -> failed`

Metadata:
- `reason=execution_error`
- `error=str(exception)`

### Approval completion failure after executor success
Path:
- `proposed -> approved -> executing -> failed`

Metadata:
- `reason=approval_completion_error`
- `error=str(exception)` from the lifecycle completion store

The executor has already returned successfully, so viewer/API mappings treat this as a side-effects-possible boundary rather than a pre-execution block.

### Resume approval block
Path:
- `proposed -> approved -> executing -> resumable -> failed`

Metadata:
- `reason=resume_approval_block`
- `error` contains the resume approval validation error, such as expiry

The executor is not re-entered for the blocked resume attempt, the live checkpoint is cleared when the run becomes terminal, and API/viewer mappings keep this as side-effects-possible because execution had already reached a checkpoint before the failed resume.

### Compensatable execution failure with successful compensation
Path:
- `proposed -> approved -> executing -> compensating -> compensated`

### Compensatable execution failure with failed compensation
Path:
- `proposed -> approved -> executing -> compensating -> compensation_failed`

### Resumable execution
First pause:
- `proposed -> approved -> executing -> resumable`

Resume attempt with live checkpoint:
- `resumable -> executing -> ...`

A resumed run may become `resumable` again, so repeated shapes like this are valid and expected:
- `proposed -> approved -> executing -> resumable -> executing -> resumable -> executing -> applied`

## 6. Run identity and idempotency semantics

The runtime binds a `run_id` to exactly one `action_id`.

If a later `run()` call uses the same `run_id` but a different `ActionEnvelope.idempotency_key`, the runtime raises `ValueError` and does not append anything.

If a later `run()` call uses the same `run_id` and same `action_id`:
- terminal runs are returned idempotently without re-execution
- resumable runs continue only when the current runtime instance still has the live checkpoint payload

## 7. API and viewer mapping

`RunViewer` and the FastAPI app expose two shapes:

### Run summary
Returned by `RunViewer.list_runs()` and `GET /runs`:

```json
{
  "run_id": "string",
  "action_id": "string",
  "state": "applied",
  "scope": "inside_mvp_scope",
  "boundaries": ["side_effects_possible", "terminal"],
  "terminal_semantics": {
    "terminal_state": "applied",
    "expected_terminal_state": "applied",
    "boundary": "applied",
    "scope_guess": "inside_mvp_scope",
    "note": "Terminal success; side effects may already exist."
  }
}
```

This is the latest journal state per run plus the derived API/viewer interpretation fields for that latest state.

### Run detail
Returned by `RunViewer.get_run()` and `GET /runs/{run_id}`:

```json
{
  "run_id": "string",
  "action_id": "string",
  "state": "applied",
  "scope": "inside_mvp_scope",
  "boundaries": ["side_effects_possible", "terminal"],
  "terminal_semantics": {
    "terminal_state": "applied",
    "expected_terminal_state": "applied",
    "boundary": "applied",
    "scope_guess": "inside_mvp_scope",
    "note": "Terminal success; side effects may already exist."
  },
  "has_checkpoint": true,
  "journal": [
    {
      "run_id": "string",
      "action_id": "string",
      "state": "applied",
      "scope": "inside_mvp_scope",
      "boundaries": ["side_effects_possible", "terminal"],
      "terminal_semantics": {
        "terminal_state": "applied",
        "expected_terminal_state": "applied",
        "boundary": "applied",
        "scope_guess": "inside_mvp_scope",
        "note": "Terminal success; side effects may already exist."
      }
    }
  ]
}
```

Actual mapping rules:
- `state` is always the latest journal state for the run.
- `journal` is the full append-ordered history for that run.
- `reason` and `error` appear on journal entries only when present.
- `has_checkpoint` is `true` only when:
  - the latest state is `resumable`, and
  - the current `Runtime` instance still has an in-memory checkpoint for that run.

### `has_checkpoint` caveat

`has_checkpoint` is not durable checkpoint persistence.

If `RunViewer` is created from a storage path instead of the original live `Runtime`, it can read persisted journal history but cannot recover another process's in-memory checkpoint payloads. In that mode:
- a run can still have latest state `resumable`
- `has_checkpoint` will be `false`

The runtime matches this caveat during execution: if the journal says `resumable` but the current runtime instance has no live checkpoint payload, `run()` returns the existing `resumable` entry unchanged and does not blindly call the executor with `checkpoint=None`.

## 8. Viewer/API-derived interpretation layer

In addition to canonical journal facts, `src/safeloop/api.py` derives three additive fields for viewer/API payloads:
- `scope`
- `boundaries`
- `terminal_semantics`

Allowed `scope` values:
- `inside_mvp_scope`
- `boundary_case`

Current derived mapping:
- `proposed` -> `inside_mvp_scope`, `[]`
- `approved` -> `inside_mvp_scope`, `[]`
- `executing` -> `boundary_case`, `["side_effects_possible"]`
- `applied` -> `inside_mvp_scope`, `["side_effects_possible", "terminal"]`
- `compensating` -> `boundary_case`, `["cleanup_attempted", "side_effects_possible"]`
- `compensated` -> `inside_mvp_scope`, `["cleanup_attempted", "side_effects_possible", "terminal"]`
- `compensation_failed` -> `boundary_case`, `["cleanup_attempted", "cleanup_incomplete_or_uncertain", "side_effects_possible", "terminal"]`
- `resumable` -> `boundary_case`, `["checkpoint_recorded", "side_effects_possible"]`
- `handed_off` -> `boundary_case`, `["pre_execution", "operator_owned", "terminal"]`
- `failed(reason=approval_block)` -> `inside_mvp_scope`, `["pre_execution", "terminal"]`
- `failed(reason=approval_error)` -> `inside_mvp_scope`, `["pre_execution", "terminal"]`
- `failed(reason=resume_approval_block)` -> `boundary_case`, `["checkpoint_recorded", "side_effects_possible", "terminal"]`
- `failed(reason=approval_completion_error)` -> `boundary_case`, `["side_effects_possible", "terminal"]`
- `failed(reason=execution_error)` -> `boundary_case`, `["side_effects_possible", "terminal"]`
- `failed(reason=None|legacy unknown)` -> `boundary_case`, `["terminal"]`

Interpretation rules:
- `state` and `reason` remain canonical runtime truth.
- `scope`, `boundaries`, and `terminal_semantics` reduce first-pass reader over-interpretation; they do not replace the journal meaning.
- `has_checkpoint` remains separate because it is a live-runtime hint, not part of persisted journal semantics.
- `outside_strict_rollback_scope` remains docs-only in the current MVP; it is not emitted as an API enum.

### `terminal_semantics` payload

`terminal_semantics` is emitted on every `Journal entry payload` returned by `RunViewer.get_run()` and `GET /runs/{run_id}/journal`. It uses the `TerminalSemantics` model:

```json
{
  "terminal_state": "applied",
  "expected_terminal_state": "applied",
  "boundary": "applied",
  "scope_guess": "inside_mvp_scope",
  "note": "Terminal success; side effects may already exist."
}
```

Fields:
- `terminal_state`: the actual `JournalState` on the entry.
- `expected_terminal_state`: currently the same value as `terminal_state`; product-adapter scenarios may use separate oracle metadata outside the persisted journal to explain expected-vs-observed terminal differences.
- `boundary`: a `TerminalBoundary` string that gives reader-safe context without rewriting the runtime state.
- `scope_guess`: the same high-level `ScopeAnnotation` produced from the journal state and reason.
- `note`: conservative operator wording for the boundary.

Current `TerminalBoundary` values:
- `proposed`
- `approved`
- `executor_boundary`
- `applied`
- `compensation_in_progress`
- `compensated`
- `compensation_failure`
- `checkpoint_resume`
- `operator_handoff`
- `approval_boundary`
- `execution_failure`
- `failed_unknown_boundary`

Important caveats:
- `checkpoint_resume` means the journal recorded a resumable point, but storage-only viewers cannot resume from it unless a live runtime still owns the checkpoint payload.
- `compensated` means mitigation completed; successful compensation does not prove perfect rollback.
- `compensation_failure` means compensation was attempted but cleanup is incomplete or uncertain; `compensation_failed` is not rollback.

## 9. Storage behavior

`LocalJournalStorage` appends one `JournalEntry.model_dump_json()` record per line.

Read behavior:
- missing journal file returns an empty list
- blank lines are ignored
- entries are returned in file order
- `list_run_ids()` preserves first-seen run order
- `read(run_id)` filters the full file to entries matching that `run_id`

This means the journal file is an append-only local event log for all runs, while runtime and API views are per-run projections over that log.
