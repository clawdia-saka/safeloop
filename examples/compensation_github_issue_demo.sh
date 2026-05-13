#!/usr/bin/env bash
set -euo pipefail

ROOT="${TMPDIR:-/tmp}/safeloop-compensation-github-$$"
RUN_DIR="$ROOT/run-github-issue"
mkdir -p "$RUN_DIR"

cat >"$RUN_DIR/run.json" <<'JSON'
{"schema_version":"run.v1","run_id":"run-github-issue","task_id":"compensation-github-issue-demo"}
JSON

cat >"$RUN_DIR/side-effects.jsonl" <<'JSONL'
{"schema_version":"side-effect-ledger.v1","event_id":"sevt-github-issue-1","run_id":"run-github-issue","created_at":"2026-05-13T00:00:00+00:00","phase":"committed","effect_class":"github_issue_comment","adapter":{"name":"github-demo","version":"local-fixture","supports_idempotency":true},"target":{"repo":"example/repo","issue_number":42},"idempotency_key":"demo-github-issue-42","external_ref":"https://github.com/example/repo/issues/42#issuecomment-demo","privacy":{"redaction":"strict","contains_secret":false,"raw_payload_persisted":false},"compensation":{"capability":"best_effort","action":"close_issue_or_post_correction_comment","operator_note":"Close the issue if it is wrong, or post a correction comment that links the superseding evidence."},"reason":"demo of GitHub issue/comment compensation boundary"}
JSONL

PYTHONPATH="${PYTHONPATH:-}:$PWD/src" python - <<PY
from safeloop.compensation import build_compensation_plan
build_compensation_plan("$RUN_DIR")
PY

echo "$RUN_DIR"
