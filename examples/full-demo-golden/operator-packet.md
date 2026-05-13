# SafeLoop full demo operator packet

Run directory: $RUN_DIR
Repository: $TMP_ROOT/repo
Local change evidence: $RUN_DIR/checkpoints/cp-0001/diff.patch
Artifact verification: $RUN_DIR/verification/verify-artifacts-result.json
Rollback plan: $RUN_DIR/rollback-plan.json
External evidence: $TMP_ROOT/fake-external-service.log

Decision boundary:
- Covered local file rollback: service.md can be rolled back after plan review.
- External action: fake ticket remains manual-review/compensation territory.
- Exact rollback is not claimed for anything outside $TMP_ROOT/repo.

Rollback command:
python -m safeloop.cli rollback apply "$RUN_DIR" "$RUN_ID" "cp-0001" --files service.md
