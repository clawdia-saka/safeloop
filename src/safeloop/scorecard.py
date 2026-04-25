"""Scorecard aggregation helpers for SafeLoop scenario sweepers."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

_UNKNOWN_REASON = "unknown"
_STRUCTURED_REASON_FIELDS = (
    "fallback_reason",
    "proposal_failure_reason",
    "proposal_error",
    "retry_error",
)
_NESTED_FAILURE_FIELDS = ("error", "message", "validation_error", "reason")


def summarize_scores(results: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, int]]:
    """Summarize proposal-source and fallback-reason counts.

    The overnight issue-sweeper records fallback scenarios when model proposals
    cannot be parsed or validated. Those records may carry structured failure
    metadata in newer runners, while older artifacts often only have a free-text
    ``why`` field. This helper prefers structured fallback context and keeps the
    legacy ``why`` fallback solely for backwards compatibility.
    """

    proposal_source_counts: Counter[str] = Counter()
    fallback_reason_counts: Counter[str] = Counter()

    for result in results:
        source = _clean(result.get("proposal_source")) or _UNKNOWN_REASON
        proposal_source_counts[source] += 1
        if source == "fallback":
            fallback_reason_counts[_fallback_reason(result)] += 1

    return {
        "proposal_source_counts": dict(proposal_source_counts),
        "fallback_reason_counts": dict(fallback_reason_counts),
    }


def _fallback_reason(result: Mapping[str, Any]) -> str:
    for field in _STRUCTURED_REASON_FIELDS:
        value = _clean(result.get(field))
        if value:
            return value

    proposal_failure = result.get("proposal_failure")
    if isinstance(proposal_failure, Mapping):
        nested = _nested_failure_reason(proposal_failure)
        if nested:
            return nested

    return _clean(result.get("why")) or _UNKNOWN_REASON


def _nested_failure_reason(proposal_failure: Mapping[str, Any]) -> str | None:
    category = _clean(proposal_failure.get("category"))
    detail = next(
        (_clean(proposal_failure.get(field)) for field in _NESTED_FAILURE_FIELDS if _clean(proposal_failure.get(field))),
        None,
    )
    if category and detail:
        return f"{category}: {detail}"
    return category or detail


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
