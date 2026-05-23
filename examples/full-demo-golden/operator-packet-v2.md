# SafeLoop Operator Packet v2

## 1. Run summary
- run_id: $RUN_ID
- task_id: full-demo
- status: completed
- started_at: $TIMESTAMP
- ended_at: $TIMESTAMP
- latest event hash: sha256:$HASH
- verification status: valid

## 2. Artifact verification
- verify-artifacts status: valid
- local anchor status: present
- evidence packet status: not_present
- issues / warnings:
  - none

## 3. Change summary
| Item | Type | Path / Ref | Status | Exact rollback | Evidence |
| --- | --- | --- | --- | --- | --- |
| service.md | local_file | service.md | rollback_available | true | rollback-plan.json |
| cp-0001 | action_group | cp-0001 | rollback_available | true | rollback-plan.json |
| $TMP_ROOT/fake-external-service.log | external_side_effect | $TMP_ROOT/fake-external-service.log | manual_review_required | false | $TMP_ROOT/fake-external-service.log |
| $TMP_ROOT/fake-external-service.log | manual_review_item | $TMP_ROOT/fake-external-service.log | queued | false | $TMP_ROOT/fake-external-service.log |
| $TMP_ROOT/fake-external-service.log | compensation_item | $TMP_ROOT/fake-external-service.log | compensation_review_required | false | $TMP_ROOT/fake-external-service.log |

## 4. Rollback decision table
| Selection | Scope | Rollback status | Exact rollback | Blockers | Suggested command |
| --- | --- | --- | --- | --- | --- |
| all covered local files | covered local file changes | available | true | none | python -m safeloop.cli rollback apply "$RUN_DIR" "$RUN_ID" "cp-0001" |
| selected file rollback | service.md | available | true | none | python -m safeloop.cli rollback apply "$RUN_DIR" "$RUN_ID" "cp-0001" --files service.md |
| selected hunk rollback | review hunk manifest before apply | review_required | true | operator must select hunk | review hunk-manifest.json, then run rollback apply with selected hunks |
| selected action group rollback | cp-0001 | available | true | none | python -m safeloop.cli rollback apply "$RUN_DIR" "$RUN_ID" "cp-0001" |

## 5. External compensation / manual review status
- runtime-tool-firewall.jsonl: not_present
- runtime-tool-exec.jsonl: not_present
- external-outbox.json: not_present
- external-effects.jsonl: not_present
- side-effects.jsonl: not_present
- legacy side-effect ledger compatibility: not_applicable
- compensation-plan.json: not_present
- compensation-result.json: not_present
- This table is separate from local rollback. It records compensation/manual review only and never exact external rollback.

## 5. Compensation decision table
Compensation capability enum: none, manual, best_effort, verified
| Side effect | Adapter | Compensation capability | Exact rollback | Required action | Evidence |
| --- | --- | --- | --- | --- | --- |
| $TMP_ROOT/fake-external-service.log | manual | manual | false | Review and compensate manually; do not treat local rollback as external rollback. | $TMP_ROOT/fake-external-service.log |

## 6. Manual review queue
| Item | Reason | Risk | Recommended operator action |
| --- | --- | --- | --- |
| $TMP_ROOT/fake-external-service.log | action outside the local repo | local rollback cannot prove the external action was undone | review evidence and execute compensation/manual handoff if needed |

## 7. Recommended next action
recommended next action: compensation_review_required

## 8. Boundary statement
- Exact rollback only applies to covered local file changes.
- External side effects are manual-review/compensation only.
- SafeLoop does not claim exact rollback for actions outside the local repo.
- GitHub, messaging, email, webhooks, hosted systems, and third-party services require compensation/manual review rather than exact rollback.
- Unknown runtime tool requests require manual review before execution.
- Guarded runtime tool execution only runs allowlisted read-only commands after firewall routing.
