#!/usr/bin/env bash
set -euo pipefail

ROOT="${TMPDIR:-/tmp}/safeloop-compensation-message-$$"
RUN_DIR="$ROOT/run-message"
PYTHON_BIN="${PYTHON:-python3}"
mkdir -p "$RUN_DIR"

cat >"$RUN_DIR/run.json" <<'JSON'
{"schema_version":"run.v1","run_id":"run-message","task_id":"compensation-message-demo"}
JSON

cat >"$RUN_DIR/side-effects.jsonl" <<'JSONL'
{"schema_version":"side-effect-ledger.v1","event_id":"sevt-message-1","run_id":"run-message","created_at":"2026-05-13T00:00:00+00:00","phase":"committed","effect_class":"message_send","adapter":{"name":"message-demo","version":"local-fixture","supports_idempotency":false},"target":{"platform":"telegram-or-slack","channel_ref":"redacted-demo-channel"},"idempotency_key":null,"external_ref":"redacted-message-id","privacy":{"redaction":"strict","contains_secret":false,"raw_payload_persisted":false},"compensation":{"capability":"manual","action":"delete_if_supported_else_send_correction","operator_note":"Verify whether deletion is available; otherwise send a correction that quotes the original message context."},"reason":"demo of message compensation boundary"}
JSONL

PYTHONPATH="${PYTHONPATH:-}:$PWD/src" "$PYTHON_BIN" - <<PY
from safeloop.compensation import build_compensation_plan
build_compensation_plan("$RUN_DIR")
PY

echo "$RUN_DIR"
