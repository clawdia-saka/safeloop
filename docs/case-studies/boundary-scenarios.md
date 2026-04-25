# Boundary scenarios and example classification

SafeLoop now exposes two kinds of example surface:

1. `examples/github_pr_demo.py` — a concrete local GitHub-style reference flow
2. `examples/boundary_demos.py` — compact runtime-backed boundary scenarios

The point of this split is to keep the GitHub case study concrete while still making the important semantic edges runnable.

## Example matrix

| Scenario | Entry point | Classification | What it shows |
| --- | --- | --- | --- |
| Success / applied | `run_github_pr_demo(..., fail_after_create=False)` | `in_scope` | Normal runtime-backed side-effecting execution finishes cleanly. |
| Compensation / compensated | `run_github_pr_demo(..., fail_after_create=True)` | `in_scope` | A compensatable action can fail after side effects begin and still complete the defined cleanup path. |
| Handoff / handed_off | `run_handoff_demo()` | `boundary` | The honest outcome may be to stop before execution and hand control to an operator/external system. |
| Compensation failure / compensation_failed | `run_compensation_failed_demo()` | `boundary` | Cleanup itself can fail; SafeLoop should say that explicitly instead of flattening everything into generic failure. |
| Resumable / resumable -> applied | `run_resumable_demo()` | `boundary` | A run can pause, hold checkpoint state in the live runtime, and later resume without pretending the first attempt never happened. |
| Repeated resume / resumable -> resumable -> applied | `run_repeated_resume_demo()` | `boundary` | Some runs need more than one checkpointed retry; the journal should show each resumable stop instead of flattening them into a single retry story. |
| Unsupported rollback expectation | `describe_unsupported_rollback_expectation()` | `unsupported` | Compensation should not be misread as perfect rollback or “as-if-never-happened” recovery. |

## Why these are examples instead of a state catalog

These examples are intentionally narrow. They do **not** attempt to provide one runnable script for every possible state transition.

What they should help a reader answer is:
- did execution begin?
- did SafeLoop stop before execution?
- did cleanup succeed?
- did cleanup fail?
- is resume possible only in the live runtime that still holds the checkpoint?

## Reader guidance

- `in_scope` means the example demonstrates a core MVP path that SafeLoop intentionally supports today.
- `boundary` means the example demonstrates an honest edge where the runtime must be precise about ownership, cleanup, or resumability.
- `unsupported` means the repo is explaining a common misread or overclaim, not promising that the current runtime solves it.

Issue #12 will later make similar scope/boundary labels explicit in the viewer/API. For issue #11, the examples and docs now carry that classification directly.
