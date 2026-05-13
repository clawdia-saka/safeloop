SafeLoop explain: rollback groups and recovery boundaries
What these words mean:
  rollback: restore covered local repo files from verified SafeLoop artifacts.
  compensation: record a cleanup or correction plan for actions outside the local repo; this is not exact rollback.
  manual handoff: an operator must review or complete work that SafeLoop cannot safely do automatically.
  action groups: related files/hunks/checkpoints bundled so an operator can review one unit of work.
Boundary: actions outside the local repo stay exact_rollback=false and require compensation or manual handoff.
run id: $RUN_ID
task id: full-demo
actions outside local repo: 0 (support=manual_review, exact_rollback=false)
grp-0001 docs update
  files: service.md
  rollback modes: checkpoint, file, hunk
  action group source: checkpoint
  risk: low
