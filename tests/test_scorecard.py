from safeloop.scorecard import summarize_scores


def test_fallback_reason_counts_use_structured_failure_context() -> None:
    scorecard = summarize_scores(
        [
            {
                "name": "fallback_parse_error",
                "proposal_source": "fallback",
                "why": "Fallback scenario text",
                "proposal_failure": {
                    "category": "parse_error",
                    "message": "no JSON object found in model output",
                },
            },
            {
                "name": "fallback_schema_error",
                "proposal_source": "fallback",
                "proposal_failure_reason": "invalid kind: unsupported cleanup claim",
            },
        ]
    )

    assert scorecard["proposal_source_counts"] == {"fallback": 2}
    assert scorecard["fallback_reason_counts"] == {
        "parse_error: no JSON object found in model output": 1,
        "invalid kind: unsupported cleanup claim": 1,
    }


def test_fallback_reason_counts_preserve_legacy_why_then_unknown() -> None:
    scorecard = summarize_scores(
        [
            {"proposal_source": "fallback", "why": "legacy parse failure text"},
            {"proposal_source": "fallback", "why": ""},
            {"proposal_source": "qwen", "proposal_failure_reason": "not a fallback"},
        ]
    )

    assert scorecard["proposal_source_counts"] == {"fallback": 2, "qwen": 1}
    assert scorecard["fallback_reason_counts"] == {
        "legacy parse failure text": 1,
        "unknown": 1,
    }


def test_empty_nested_failure_context_falls_back_to_legacy_reason() -> None:
    scorecard = summarize_scores(
        [
            {
                "proposal_source": "fallback",
                "proposal_failure": {"category": " ", "message": None},
                "why": "legacy fallback detail",
            }
        ]
    )

    assert scorecard["fallback_reason_counts"] == {"legacy fallback detail": 1}
