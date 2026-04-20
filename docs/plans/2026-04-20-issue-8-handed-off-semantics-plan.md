# Plan: Issue 8 HANDED_OFF semantics and operator boundary

1. Add regression tests for the intended handoff semantics:
   - reject `EXECUTING -> HANDED_OFF`
   - confirm escalation is pre-execution and skips compensation
2. Update transition graph/runtime docs to match the intended semantics.
3. Run focused tests for journal/runtime/api.
4. Run full test suite.
5. Run Qwen structured review, fix any important findings, then open PR and comment on the issue.
