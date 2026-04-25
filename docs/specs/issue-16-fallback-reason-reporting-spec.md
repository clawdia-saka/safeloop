# Issue #16: fallback reason reporting

## Decision
Scorecard summarization must count structured fallback failure context before falling back to legacy free-text fields. A fallback result with concrete parser/schema/validation metadata must not collapse to `unknown`.

## Data contract
A result record may contain any of these structured fields:
- `fallback_reason`
- `proposal_failure_reason`
- `proposal_failure.error`
- `proposal_failure.message`
- `proposal_failure.category`
- `proposal_failure.validation_error`
- `proposal_error`
- `retry_error`

The summarizer normalizes the first non-empty value to a stable string. For nested `proposal_failure`, `category` is prefixed to the first non-empty detail field (`error`, `message`, `validation_error`, or `reason`); if only one side is present, that value is used alone. Legacy records without structured context may still use `why`; records with neither structured context nor `why` count as `unknown`.

## Acceptance criteria
- `proposal_source_counts` remains a count of `proposal_source` values.
- `fallback_reason_counts` contains concrete non-`unknown` keys when fallback records carry structured failure context.
- Structured context takes precedence over generic `why` text.
- Backward compatibility remains for old result records that only carry `why`.
