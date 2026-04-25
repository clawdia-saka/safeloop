# Plan: Issue 17 global semantic duplicate guard

1. Add RED tests for run-wide duplicate tracking and fingerprint stability.
2. Implement a dependency-free `ScenarioDedupeGuard` utility.
3. Run focused tests, then the full suite.
4. Run Qwen structured review before commit/PR.
