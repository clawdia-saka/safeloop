# Case study: GitHub pull request demo

## Scenario

An agent wants to create a pull request.
That is a side effect, not just a thought.

In SafeLoop terms, the action should be explicit before it runs:

- action name: `github.create_pull_request`
- target: repository
- effect class: `compensatable_write`
- identity: stable idempotency key

That effect label matters. In this demo, creating a PR is treated as **compensatable**, not **reversible**:

| Label | What to read it as in this demo | What not to infer |
| --- | --- | --- |
| `reversible_write` | The platform could truly undo the write itself. | This demo does not make that stronger claim. |
| `compensatable_write` | If later execution fails, SafeLoop can try a named cleanup action such as `close_pr`. | Do not read this as “GitHub state is rolled back to exactly as before.” |

## Success path

A successful run should look like:

- `proposed`
- `approved`
- `executing`
- `applied`

This tells an operator that the action really made it through execution and finished cleanly.

## Failure + compensation path

If the pull request is created but a later step fails, the right question is not “did the code throw?” but:

- did the PR already get created?
- should the system retry?
- should it compensate?
- what state is the run in now?

In the GitHub-style demo, compensation is modeled as `close_pr`.
That is intentionally a compensation story, not a literal rollback story: the original PR may have existed, notifications may already have fired, and outside observers may already have seen the side effect.

That path becomes:

- `proposed`
- `approved`
- `executing`
- `compensating`
- `compensated`

If the cleanup step itself fails, the runtime should say so explicitly rather than flattening everything into generic failure:

- `proposed`
- `approved`
- `executing`
- `compensating`
- `compensation_failed`

That means SafeLoop attempted the compensation hook path, but the cleanup path did not complete successfully. It does **not** mean rollback was guaranteed and then somehow hidden, and it should not be misread as equivalent to generic `failed` either.

## Why this matters

The GitHub pull-request flow should stay a concrete reference case, not a dumping ground for every boundary scenario SafeLoop knows about.

That is why the repository now splits examples into two layers:
- `examples/github_pr_demo.py` for the concrete local GitHub-style reference flow
- `examples/boundary_demos.py` for smaller runtime-backed boundary scenarios such as `handed_off`, `compensation_failed`, and `resumable`

The broader example matrix and current `in_scope` / `boundary` / `unsupported` classification now live in [`boundary-scenarios.md`](boundary-scenarios.md).

Without an action model and journal, a team often ends up inferring recovery from scattered logs.

With SafeLoop, the intent is different:

- action intent is explicit before execution
- lifecycle state is journaled by the runtime
- compensation is a named path, not an improvised cleanup guess
- operators can inspect the resulting run state through `RunViewer` or the HTTP API against the same persisted journal truth

## What this case study does not claim

This demo is still local/reference-only and uses fake GitHub-side behavior at the boundary, but it is now **runtime-backed** inside the repository.

It does **not** prove:

- live GitHub integration
- durable distributed recovery
- exact rollback of every GitHub-visible side effect
- production-grade incident handling

What it does prove is the narrower point SafeLoop cares about:

- the example action executes through the real runtime rather than a parallel toy state machine
- success and compensation paths are persisted into journal history
- the same run can be inspected through viewer/API surfaces using persisted local storage

> side-effecting agent actions should have an explicit execution and recovery model, not just a success/failure boolean.
