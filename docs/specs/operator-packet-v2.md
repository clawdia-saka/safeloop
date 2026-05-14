# SafeLoop Operator Packet v2

Operator packet v2 upgrades the v1 demo/spec artifact into a deterministic decision table. It helps an operator answer:

1. what happened,
2. what can be exactly rolled back,
3. what requires compensation,
4. what requires manual review,
5. what command to run next.

The packet is local and file-backed. It does not add new rollback runtime behavior, hosted control-plane behavior, remote transparency logging, real third-party compensation execution, or auto-remediation.

## 1. Run summary

Required fields:

- run_id
- task_id
- status
- started_at / ended_at
- latest event hash
- verification status

## 2. Artifact verification

Required fields:

- verify-artifacts status
- local anchor status
- evidence packet status if present
- issues / warnings

## 3. Change summary

| Item | Type | Path / Ref | Status | Exact rollback | Evidence |
|---|---|---|---|---|---|
| service.md | local_file | service.md | rollback_available | true | rollback-plan.json |
| cp-0001 | action_group | cp-0001 | rollback_available | true | rollback-plan.json |
| fake-ticket | external_side_effect | external-evidence.log | manual_review_required | false | external-evidence.log |

Types:

- local_file
- hunk
- action_group
- external_side_effect
- compensation_item
- manual_review_item

## 4. Rollback decision table

| Selection | Scope | Rollback status | Exact rollback | Blockers | Suggested command |
|---|---|---|---|---|---|
| all covered local files | covered local file changes | available | true | none | `python -m safeloop.cli rollback apply RUN_DIR RUN_ID CHECKPOINT_ID` |
| selected file rollback | service.md | available | true | none | `python -m safeloop.cli rollback apply RUN_DIR RUN_ID CHECKPOINT_ID --files service.md` |
| selected hunk rollback | review hunk manifest before apply | review_required | true | operator must select hunk | review `hunk-manifest.json`, then run rollback apply with selected hunks |
| selected action group rollback | cp-0001 | available | true | none | `python -m safeloop.cli rollback apply RUN_DIR RUN_ID CHECKPOINT_ID` |

## 5. External compensation / manual review status

When present, the packet surfaces these file-backed external artifacts without importing compensation APIs:

- `external-effects.jsonl`: external registry; status is present/not_present.
- `compensation-plan.json`: planned compensation artifact; status is read from its top-level `status` when present.
- `compensation-result.json`: compensation result/receipt artifact; status is read from its top-level `status` when present.

This section is separate from the local rollback decision table. It records compensation/manual review only and never exact external rollback.

## 5. Compensation decision table

Compensation capability enum: none, manual, best_effort, verified

| Side effect | Adapter | Compensation capability | Exact rollback | Required action | Evidence |
|---|---|---|---|---|---|
| fake-ticket | manual | manual | false | Review and compensate manually; do not treat local rollback as external rollback. | external-evidence.log |

## 6. Manual review queue

| Item | Reason | Risk | Recommended operator action |
|---|---|---|---|
| fake-ticket | action outside the local repo | local rollback cannot prove the external action was undone | review evidence and execute compensation/manual handoff if needed |

## 7. Recommended next action

One of:

- verify_only
- rollback_available
- compensation_review_required
- manual_review_required
- blocked

## 8. Boundary statement

- Exact rollback only applies to covered local file changes.
- External side effects are manual-review/compensation only.
- SafeLoop does not claim exact rollback for actions outside the local repo.
- GitHub, messaging, email, webhooks, hosted systems, and third-party services require compensation/manual review rather than exact rollback.
