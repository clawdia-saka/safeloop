# SafeLoop 0.0.5 Side-Effect Ledger and Adapter Contracts

Status: proposed future contract/spec only  
Base: `feat/safeloop-003-agent-watchdog-rc` / SafeLoop 0.0.3 RC  
Parallelism note: this document intentionally avoids runtime implementation changes while 0.0.4 watchdog reliability work proceeds.

## Goal

SafeLoop 0.0.5 should turn the 0.0.3 watchdog's always-present `side-effects.jsonl` placeholder into a stable, private-first contract for recording external side-effect intent, observation, compensation metadata, and adapter boundaries.

The release should answer:

- What external action did the agent intend to perform?
- Which adapter, if any, executed or observed it?
- What durable identifier can an operator use to audit or compensate it?
- What private data must never be written to local artifacts by default?
- How does this ledger compose with 0.0.4 watchdog reliability without changing watchdog process supervision semantics?

## Non-goals for 0.0.5

- No network dashboard, hosted control plane, remote transparency log, or SaaS sync.
- No real Slack/GitHub/Vercel/Stripe writes in tests.
- No changes to process supervision, timeout, signal, retry, or checkpoint behavior owned by 0.0.4.
- No secrets, tokens, full request payloads, or raw response bodies in default artifacts.
- No claim that every external side effect can be undone.

## Contract surface

### Artifact

`side-effects.jsonl` remains local to each watchdog run artifact packet. In 0.0.5 it becomes an append-only JSONL stream with one JSON object per side-effect event.

Required top-level fields:

| Field | Type | Notes |
| --- | --- | --- |
| `schema_version` | string | Initial value: `side-effect-ledger.v1`. |
| `event_id` | string | Stable unique id within the run. |
| `run_id` | string | The watchdog/runtime run that emitted the event. |
| `created_at` | string | UTC ISO-8601 timestamp. |
| `phase` | string | `intent`, `prepared`, `committed`, `observed`, `compensating`, `compensated`, `failed`, or `redacted`. |
| `effect_class` | string | Coarse class such as `file`, `git`, `http`, `email`, `chat`, `payment`, `deploy`, or `unknown`. |
| `adapter` | object | Adapter identity and version; may be `{ "name": "local", "version": "builtin" }`. |
| `target` | object | Redacted destination descriptor. |
| `idempotency_key` | string/null | Required for commit-capable adapters when available. |
| `external_ref` | string/null | Redacted external id, URL, commit SHA, message id, job id, etc. |
| `privacy` | object | Redaction policy and data classification summary. |
| `compensation` | object | Compensation capability and operator notes. |
| `reason` | string | Machine-readable reason category. |

### Privacy defaults

0.0.5 remains private-first:

- `target` must store descriptors, not secrets: hostnames, repo names, issue numbers, or redacted handles are acceptable; tokens and request bodies are not.
- `privacy.redaction` defaults to `strict`.
- `privacy.contains_secret` must be `false` for persisted records; if an adapter cannot prove this, it must emit a `redacted` event or refuse to persist details.
- Raw request/response capture is opt-in and out of scope for the default 0.0.5 contract.

### Adapter model

Adapters are thin boundaries around effectful systems. A future implementation can expose an interface equivalent to:

```python
class SideEffectAdapter:
    name: str
    version: str
    effect_class: str

    def prepare(self, intent): ...      # validates and returns redacted prepared metadata
    def commit(self, prepared): ...     # performs or observes the side effect
    def compensate(self, external_ref): ...  # best-effort optional cleanup
```

Contract requirements:

- Adapters must be deterministic in their redaction behavior.
- Adapters must provide an `idempotency_key` when the target system supports idempotency.
- Adapters must distinguish `prepared` from `committed`; a prepared event is not proof of execution.
- Compensation support must be explicit: `none`, `manual`, `best_effort`, or `verified`.
- Unknown or unsupported side effects must still be representable as `effect_class: "unknown"` with strict redaction.

## Interaction with 0.0.4

0.0.4 is expected to harden watchdog reliability. To avoid conflicts:

- 0.0.5 must not change watchdog process lifecycle implementation while 0.0.4 is active.
- 0.0.5 should consume the run identity, artifact root, and timeline binding points that 0.0.4 stabilizes.
- 0.0.5 can define the side-effect ledger schema independently because 0.0.3 already writes an always-present placeholder file.
- If 0.0.4 changes artifact write ordering, 0.0.5 should adapt at implementation time without changing this schema's event semantics.

## Future contract tests (not implemented on this branch)

When implementation begins, add tests marked as future/contract until enabled:

1. `test_side_effect_ledger_records_strict_redacted_intent` — verifies required fields and no secret-like values persist.
2. `test_adapter_prepare_does_not_imply_commit` — verifies prepared-only runs are not reported as executed side effects.
3. `test_committed_event_requires_adapter_identity_and_external_ref_or_reason` — verifies auditability for executed/observed effects.
4. `test_compensation_capability_is_explicit` — verifies compensation metadata is always present and never implied.
5. `test_unknown_side_effect_is_representable_without_raw_payload` — verifies unsupported effects remain inspectable but private.
6. `test_ledger_events_bind_to_run_artifact_packet` — verifies ledger records reference the same run id and artifact root as watchdog output.

These tests should live separately from 0.0.4 reliability tests and should not assert process timeout/retry/signal behavior.

## Proposed 0.0.5 acceptance criteria

- `side-effects.jsonl` has a documented `side-effect-ledger.v1` schema.
- Local sample records cover at least one local/git-style effect and one redacted unknown/http-style effect without making network calls.
- Inspection output can summarize side effects without leaking raw payloads.
- Adapter contracts define prepare/commit/compensate boundaries and idempotency handling.
- Existing 0.0.3 watchdog RC behavior and 0.0.4 reliability semantics remain compatible.
