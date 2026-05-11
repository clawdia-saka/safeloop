#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
WORK=${SAFELOOP_DEMO_WORKDIR:-$(mktemp -d)}
REPO="$WORK/repo"
RUNS="$WORK/runs"
mkdir -p "$REPO" "$RUNS"

export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
cd "$REPO"
python - <<'PY'
from pathlib import Path
before = ''.join(f'line {i}\n' for i in range(1, 61))
Path('notes.txt').write_text(before)
Path('config.yml').write_text('enabled: false\nmode: safe\n')
Path('agent.py').write_text("""
from pathlib import Path
notes = Path('notes.txt')
text = notes.read_text()
notes.write_text(text.replace('line 2\\n', 'LINE 2 changed by agent\\n').replace('line 55\\n', 'LINE 55 changed by agent\\n'))
Path('config.yml').write_text('enabled: true\\nmode: risky\\n')
Path('new.txt').write_text('agent-created local artifact\\n')
""")
PY

watch_out=$(python -m safeloop.cli watch --loop --task-id rollback-selective-demo --repo "$REPO" --run-root "$RUNS" -- python agent.py)
printf '%s\n' "$watch_out"
RUN_DIR=$(printf '%s\n' "$watch_out" | awk -F': ' '/^Run dir:/ {print $2}')
RUN_ID=$(python - <<PY
import json, pathlib
print(json.loads(pathlib.Path('$RUN_DIR/run.json').read_text())['run_id'])
PY
)

python -m safeloop.cli review "$RUN_DIR"
python -m safeloop.cli review "$RUN_DIR" --groups
python -m safeloop.cli explain "$RUN_DIR"
cat > "$WORK/policy.json" <<'JSON'
{"schema_version":"do-not-do-policy.v1","policies":[{"policy_id":"demo-config","paths":["config.yml"]}]}
JSON
python -m safeloop.cli policy-check "$RUN_DIR" --policy "$WORK/policy.json" --json >/dev/null || true
# Verification commands are part of the readiness packet. Some review/explain commands intentionally
# write derived local artifacts, so this demo keeps verification as an operator-visible step rather
# than a hard gate for later derived-artifact mutations.
python -m safeloop.cli verify-artifacts "$RUN_DIR" >/dev/null || true
python -m safeloop.cli verify-anchor "$RUN_DIR" >/dev/null || true

# Re-run from the original baseline for rollback-to-start, selected-file, and selected-hunk paths.
python - <<'PY'
from pathlib import Path
before = ''.join(f'line {i}\n' for i in range(1, 61))
Path('notes.txt').write_text(before)
Path('config.yml').write_text('enabled: false\nmode: safe\n')
Path('new.txt').unlink(missing_ok=True)
PY
watch_out=$(python -m safeloop.cli watch-run --task-id rollback-to-start-demo --repo "$REPO" --run-root "$RUNS" -- python agent.py)
RUN_DIR=$(printf '%s\n' "$watch_out" | awk -F': ' '/^Run dir:/ {print $2}')
RUN_ID=$(python - <<PY
import json, pathlib
print(json.loads(pathlib.Path('$RUN_DIR/run.json').read_text())['run_id'])
PY
)
python -m safeloop.cli rollback plan "$RUN_DIR" "$RUN_ID" --to-start
python -m safeloop.cli rollback apply "$RUN_DIR" "$RUN_ID" --to-start

python - <<'PY'
from pathlib import Path
before = ''.join(f'line {i}\n' for i in range(1, 61))
Path('notes.txt').write_text(before)
Path('config.yml').write_text('enabled: false\nmode: safe\n')
Path('new.txt').unlink(missing_ok=True)
PY
watch_out=$(python -m safeloop.cli watch-run --task-id rollback-files-demo --repo "$REPO" --run-root "$RUNS" -- python agent.py)
RUN_DIR=$(printf '%s\n' "$watch_out" | awk -F': ' '/^Run dir:/ {print $2}')
RUN_ID=$(python - <<PY
import json, pathlib
print(json.loads(pathlib.Path('$RUN_DIR/run.json').read_text())['run_id'])
PY
)
python -m safeloop.cli rollback plan "$RUN_DIR" "$RUN_ID" --files config.yml
python -m safeloop.cli rollback apply "$RUN_DIR" "$RUN_ID" --files config.yml

python - <<'PY'
from pathlib import Path
before = ''.join(f'line {i}\n' for i in range(1, 61))
Path('notes.txt').write_text(before)
Path('config.yml').write_text('enabled: false\nmode: safe\n')
Path('new.txt').unlink(missing_ok=True)
PY
watch_out=$(python -m safeloop.cli watch-run --task-id rollback-hunks-demo --repo "$REPO" --run-root "$RUNS" -- python agent.py)
RUN_DIR=$(printf '%s\n' "$watch_out" | awk -F': ' '/^Run dir:/ {print $2}')
RUN_ID=$(python - <<PY
import json, pathlib
print(json.loads(pathlib.Path('$RUN_DIR/run.json').read_text())['run_id'])
PY
)
python -m safeloop.cli rollback plan "$RUN_DIR" "$RUN_ID" --hunks hunk-0001
python -m safeloop.cli rollback apply "$RUN_DIR" "$RUN_ID" --hunks hunk-0001

python - <<'PY'
from pathlib import Path
before = ''.join(f'line {i}\n' for i in range(1, 61))
Path('notes.txt').write_text(before)
Path('config.yml').write_text('enabled: false\nmode: safe\n')
Path('new.txt').unlink(missing_ok=True)
Path('action_agent.py').write_text('''
from pathlib import Path
from safeloop.action_span import action_span
text = Path('notes.txt').read_text()
with action_span('demo action early edit', intent='show selective action rollback'):
    Path('notes.txt').write_text(text.replace('line 2\\n', 'LINE 2 changed by action\\n'))
text = Path('notes.txt').read_text()
Path('notes.txt').write_text(text.replace('line 55\\n', 'LINE 55 unrelated change\\n'))
''')
PY
watch_out=$(python -m safeloop.cli watch-run --task-id rollback-action-demo --repo "$REPO" --run-root "$RUNS" -- python action_agent.py)
RUN_DIR=$(printf '%s\n' "$watch_out" | awk -F': ' '/^Run dir:/ {print $2}')
RUN_ID=$(python - <<PY
import json, pathlib
print(json.loads(pathlib.Path('$RUN_DIR/run.json').read_text())['run_id'])
PY
)
ACTION_ID=$(python - <<PY
import json, pathlib
for line in pathlib.Path('$RUN_DIR/action-events.jsonl').read_text().splitlines():
    event = json.loads(line)
    if event.get('action_id'):
        print(event['action_id'])
        break
PY
)
python - <<PY
from pathlib import Path
from safeloop.side_effect_ledger import LocalSideEffectLedger, SideEffectAdapterIdentity, SideEffectRecord
run_dir = Path('$RUN_DIR')
ledger = LocalSideEffectLedger(run_dir / 'side-effects.jsonl', '$RUN_ID')
ledger.append(SideEffectRecord(
    phase='committed',
    effect_class='chat',
    adapter=SideEffectAdapterIdentity('local/demo', 'rollback-selective-demo', supports_idempotency=True),
    target={'channel': 'demo', 'action_id': '$ACTION_ID'},
    reason='demo external side effect placeholder',
    idempotency_key='demo-$ACTION_ID',
    external_ref='demo-ref',
    compensation={'capability': 'manual'},
))
PY
SIDE_EFFECT_ID=$(python - <<PY
import json, pathlib
print(json.loads(pathlib.Path('$RUN_DIR/side-effects.jsonl').read_text().splitlines()[-1])['event_id'])
PY
)
python -m safeloop.cli compensate "$RUN_DIR" --side-effect "$SIDE_EFFECT_ID" --dry-run >/dev/null
python -m safeloop.cli rollback plan "$RUN_DIR" "$RUN_ID" --action "$ACTION_ID" --include-compensation
python -m safeloop.cli rollback apply "$RUN_DIR" "$RUN_ID" --action "$ACTION_ID"
python -m safeloop.cli policy-check "$RUN_DIR" --policy "$WORK/policy.json" --suggest-rollback >/dev/null || true
python -m safeloop.cli rollback plan "$RUN_DIR" "$RUN_ID" --policy "$WORK/policy.json" >/dev/null || true

echo "rollback selective demo: ok ($WORK)"
