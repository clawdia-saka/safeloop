# Case study: GitHub pull request demo

## Scenario

An agent wants to create a pull request.
That is a side effect, not just a thought.

In SafeLoop terms, the action should be explicit before it runs:

- action name: `github.create_pull_request`
- target: repository
- effect class: `compensatable_write`
- identity: stable idempotency key

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

That path becomes:

- `proposed`
- `approved`
- `executing`
- `compensating`
- `compensated`

## Why this matters

Without an action model and journal, a team often ends up inferring recovery from scattered logs.

With SafeLoop, the intent is different:

- action intent is explicit before execution
- lifecycle state is journaled
- compensation is a named path, not an improvised cleanup guess
- operators can inspect the resulting run state through the same shared model

## What this case study does not claim

This demo is still mocked and local-safe.

It does **not** prove:

- live GitHub integration
- durable distributed recovery
- perfect rollback
- production-grade incident handling

What it does prove is the narrower point SafeLoop cares about:

> side-effecting agent actions should have an explicit execution and recovery model, not just a success/failure boolean.
