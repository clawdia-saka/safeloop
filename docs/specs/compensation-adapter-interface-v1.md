# Compensation Adapter Interface v1

Status: interface-only contract for roadmap slice #113.

## Scope

SafeLoop compensation adapters describe local compensation capability for an
external side effect or compensation-plan item. They do **not** execute
compensation.

Out of scope:

- no real external adapters;
- no network calls;
- no hosted control plane dependency;
- no automatic remediation;
- no local rollback behavior change;
- no exact rollback claim for external effects.

## Contract

Module: `safeloop.compensation_adapters`

Primary types:

- `AdapterCapability`: stable vocabulary: `none`, `manual`, `best_effort`, `verified`.
- `EvidenceRequirement`: local evidence an operator must collect before closing review.
- `RetryGuidance`: advisory idempotency/retry fields for a future external executor.
- `CompensationAdapterResult`: local result returned by an adapter evaluator.
- `CompensationAdapter`: protocol with `evaluate(plan_item) -> CompensationAdapterResult`.

`CompensationAdapterResult` invariants:

- `schema_version` is `compensation-adapter-result.v1`.
- `exact_rollback` is always `false`.
- `requires_manual_review` is always `true` for external compensation.
- `evidence_requirements` must contain at least one item.
- `performed_external_call` is always `false`.
- `network_calls` is always `false`.
- `idempotency_key` is present or derived from adapter/effect identity.
- `retry_guidance` is explicit even when automatic retries are disabled.

## Idempotency and retry guidance

Adapters must remove ambiguity for any later operator or executor:

- include a stable `idempotency_key` scoped to the run/effect/adapter when known;
- default to `retryable=false`, `max_attempts=0`, and `backoff_seconds=0` because SafeLoop does not retry external compensation automatically;
- if future tooling uses non-zero retry guidance, it must still require operator approval and evidence, and it must preserve `exact_rollback=false`.

## Evidence and review

A result is not a proof of compensation. It is an operator-facing local record of
what evidence is required. Operators should attach system-specific evidence such
as a durable URL, exported receipt, screenshot path, or audit log quote in the
run artifacts before marking review complete.

## Determinism and side effects

The default evaluator `evaluate_compensation_adapter(plan_item, adapter_name=...)`
only inspects the provided dictionary and returns a dataclass result. It must not
open sockets, call service SDKs, mutate rollback state, or write local files.
