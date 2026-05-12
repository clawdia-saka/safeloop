#!/usr/bin/env bash
set -euo pipefail

DEMO_REPO="${SAFELOOP_DEMO_REPO:-/tmp/safeloop-recoverability-demo-repo}"
RUN_ROOT="${SAFELOOP_DEMO_RUN_ROOT:-/tmp/safeloop-recoverability-demo-runs}"
EXTERNAL_LOG="${SAFELOOP_DEMO_EXTERNAL_LOG:-/tmp/safeloop-recoverability-external-api.log}"

rm -rf "$DEMO_REPO" "$RUN_ROOT" "$EXTERNAL_LOG"
mkdir -p "$DEMO_REPO"
cd "$DEMO_REPO"

git init -q
git config user.email demo@example.test
git config user.name demo
printf 'base\n' > note.txt
git add note.txt && git commit -q -m init

safeloop watch-run --task-id recoverability-demo --repo "$PWD" --run-root "$RUN_ROOT" -- \
  python -c "from pathlib import Path; Path('note.txt').write_text('changed by agent\\n'); Path('$EXTERNAL_LOG').write_text('fake-api-call: sent outside repo; external_review_required\\n')"

RUN_DIR="$(find "$RUN_ROOT" -maxdepth 1 -type d -name 'run-*' | head -1)"
RUN_ID="$(basename "$RUN_DIR")"

safeloop timeline "$RUN_DIR"
safeloop verify-artifacts "$RUN_DIR"
safeloop rollback plan "$RUN_DIR" "$RUN_ID" --files note.txt
safeloop rollback apply "$RUN_DIR" "$RUN_ID" --files note.txt

printf '\nLocal file after rollback: '
cat note.txt
printf 'External side-effect evidence: '
cat "$EXTERNAL_LOG"
printf '\nBoundary: local file rollback succeeded; fake external API evidence still needs manual review/compensation.\n'
