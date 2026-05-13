#!/usr/bin/env bash
set -euo pipefail

# Full public-packet demo for SafeLoop 0.1.4.
# Scope is intentionally local: SafeLoop rolls back covered repo files only.
# The simulated outside action is recorded for compensation/manual handoff, not exact rollback.

TMP_ROOT="${SAFELOOP_FULL_DEMO_ROOT:-${TMPDIR:-/tmp}/safeloop-full-demo-$$}"
DEMO_REPO="$TMP_ROOT/repo"
RUN_ROOT="$TMP_ROOT/runs"
EXTERNAL_LOG="$TMP_ROOT/fake-external-service.log"
WATCH_OUTPUT="$TMP_ROOT/watch-run.out"
OPERATOR_PACKET="$TMP_ROOT/operator-packet.md"

rm -rf "$TMP_ROOT"
mkdir -p "$DEMO_REPO" "$RUN_ROOT"

cleanup() {
  printf '\nFull demo workspace retained at: %s\n' "$TMP_ROOT"
}
trap cleanup EXIT

(
  cd "$DEMO_REPO"
  git init -q
  git config user.email demo@example.test
  git config user.name demo
  printf 'status: stable\n' > service.md
  git add service.md
  git commit -q -m init
)

cat > "$DEMO_REPO/agent.py" <<'PY'
from pathlib import Path
import os

Path("service.md").write_text(
    "status: changed by agent\n"
    "handoff: operator must review external-service evidence\n",
    encoding="utf-8",
)
Path(os.environ["EXTERNAL_LOG"]).write_text(
    "fake-ticket: created outside repo; external_review_required; exact_rollback=false\n",
    encoding="utf-8",
)
print("agent changed service.md and simulated an external ticket")
PY

python -m safeloop.cli watch-run \
  --task-id full-demo \
  --repo "$DEMO_REPO" \
  --run-root "$RUN_ROOT" \
  -- bash -c "cd '$DEMO_REPO' && EXTERNAL_LOG='$EXTERNAL_LOG' python agent.py" | tee "$WATCH_OUTPUT"

RUN_DIR="$(python - "$WATCH_OUTPUT" <<'PY'
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
RUN_ID="$(python - "$RUN_DIR/run.json" <<'PY'
import json, sys
from pathlib import Path
print(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["run_id"])
PY
)"
CHECKPOINT_ID="$(python - "$RUN_DIR/run.json" <<'PY'
import json, sys
from pathlib import Path
count = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["checkpoint_count"]
print(f"cp-{count:04d}")
PY
)"

python -m safeloop.cli timeline "$RUN_DIR"
python -m safeloop.cli verify-artifacts "$RUN_DIR"
python -m safeloop.cli review "$RUN_DIR"
python -m safeloop.cli rollback plan "$RUN_DIR" "$RUN_ID" "$CHECKPOINT_ID" --files service.md

cat > "$OPERATOR_PACKET" <<MD
# SafeLoop full demo operator packet

Run directory: $RUN_DIR
Repository: $DEMO_REPO
Local change evidence: $RUN_DIR/checkpoints/$CHECKPOINT_ID/diff.patch
Artifact verification: $RUN_DIR/verification/verify-artifacts-result.json
Rollback plan: $RUN_DIR/rollback-plan.json
External evidence: $EXTERNAL_LOG

Decision boundary:
- Covered local file rollback: service.md can be rolled back after plan review.
- External action: fake ticket remains manual-review/compensation territory.
- Exact rollback is not claimed for anything outside $DEMO_REPO.

Rollback command:
python -m safeloop.cli rollback apply "$RUN_DIR" "$RUN_ID" "$CHECKPOINT_ID" --files service.md
MD
python -m safeloop.cli rollback apply "$RUN_DIR" "$RUN_ID" "$CHECKPOINT_ID" --files service.md
python - <<PY
from pathlib import Path
from safeloop.operator_packet import write_operator_packet_v2
write_operator_packet_v2(
    Path("$RUN_DIR"),
    output_path=Path("$RUN_DIR") / "operator-packet-v2.md",
    external_evidence=["$EXTERNAL_LOG"],
    compensation_adapter="manual",
)
PY
# Copy the v1 packet after rollback because it is an operator attachment, not a
# hash-chain artifact bound by verify-artifacts. The v2 packet is generated from
# local run artifacts and the simulated outside-action evidence.
cp "$OPERATOR_PACKET" "$RUN_DIR/operator-packet.md"
python scripts/public_readiness.py --check

printf '\nLocal file after rollback:\n'
cat "$DEMO_REPO/service.md"
printf '\nExternal evidence requiring handoff:\n'
cat "$EXTERNAL_LOG"
printf '\nOperator packet: %s\n' "$RUN_DIR/operator-packet.md"
printf 'Operator packet v2: %s\n' "$RUN_DIR/operator-packet-v2.md"
printf 'Full demo complete: local rollback verified; external effect requires manual review/compensation.\n'
