#!/usr/bin/env bash
set -euo pipefail

# SafeLoop 0.0.3 RC demo: watch-run → timeline → verify-artifacts → undo --dry-run.
# Runs in a temporary repo and leaves the generated SafeLoop run packet in a temp run root.

TMP_ROOT="${TMPDIR:-/tmp}/safeloop-watchdog-demo-$$"
DEMO_REPO="$TMP_ROOT/repo"
RUN_ROOT="$TMP_ROOT/runs"
PYTHON_BIN="${PYTHON:-python3}"
mkdir -p "$DEMO_REPO" "$RUN_ROOT"

cleanup() {
  printf '\nDemo workspace retained at: %s\n' "$TMP_ROOT"
}
trap cleanup EXIT

cat > "$DEMO_REPO/agent.py" <<'PY'
from pathlib import Path

Path("agent-output.txt").write_text("hello from safeloop watchdog demo\n", encoding="utf-8")
print("agent wrote agent-output.txt")
PY

WATCH_OUTPUT="$TMP_ROOT/watch-run.out"
"$PYTHON_BIN" -m safeloop.cli watch-run \
  --task-id demo \
  --repo "$DEMO_REPO" \
  --run-root "$RUN_ROOT" \
  -- "$PYTHON_BIN" "$DEMO_REPO/agent.py" | tee "$WATCH_OUTPUT"

RUN_DIR="$("$PYTHON_BIN" - "$WATCH_OUTPUT" <<'PY'
import sys
from pathlib import Path
for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    if line.startswith("Run dir: "):
        print(line.removeprefix("Run dir: "))
        break
else:
    raise SystemExit("Run dir not found in watch-run output")
PY
)"
RUN_ID="$("$PYTHON_BIN" - "$RUN_DIR/run.json" <<'PY'
import json, sys
from pathlib import Path
print(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["run_id"])
PY
)"
CHECKPOINT_ID="$("$PYTHON_BIN" - "$RUN_DIR/run.json" <<'PY'
import json, sys
from pathlib import Path
count = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["checkpoint_count"]
print(f"cp-{count:04d}")
PY
)"

"$PYTHON_BIN" -m safeloop.cli timeline "$RUN_DIR"
"$PYTHON_BIN" -m safeloop.cli verify-artifacts "$RUN_DIR"
"$PYTHON_BIN" -m safeloop.cli undo "$RUN_DIR" "$RUN_ID" "$CHECKPOINT_ID" --dry-run
