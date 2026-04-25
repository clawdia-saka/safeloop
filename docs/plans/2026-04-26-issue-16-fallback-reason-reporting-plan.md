# Plan: Issue #16 fallback reason reporting

1. Add focused RED tests for fallback scorecard aggregation.
2. Add a small `safeloop.scorecard` helper with deterministic `summarize_scores()` behavior.
3. Export the helper for runner/overnight-lite consumers.
4. Run focused scorecard tests, then full pytest.
5. Run Qwen structured review before commit/PR.
