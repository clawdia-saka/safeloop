#!/usr/bin/env python3
"""Local-only browser/API-like action SafeLoop example fixture."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path(".safeloop-real-world/browser-api-action-run"))
    args = parser.parse_args()
    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)

    requested_action = {
        "kind": "outside_action_fixture",
        "target": "local-fixture://crm/tickets/123/comment",
        "intent": "post customer-visible comment",
        "payload_preview": "We are investigating and will update shortly.",
    }
    blocked = {
        "schema_version": "blocked-outside-action.v1",
        "requested_action": requested_action,
        "status": "blocked_manual_review",
        "reason": "Outside/browser/API-like action is represented by a local fixture and requires operator approval.",
        "exact_rollback": False,
    }
    (out / "blocked-action.json").write_text(json.dumps(blocked, indent=2) + "\n")
    (out / "manual-review.md").write_text(
        "# Manual review required\n\n"
        "SafeLoop did not perform the outside action. An operator must review the local fixture, "
        "decide whether to execute it in the real system, and record any compensation plan.\n\n"
        "exact_rollback: false\n"
    )

    summary = {
        "scenario": "browser_api_action_run",
        "artifact_dir": str(out),
        "network": "disabled/not used",
        "exact_rollback": False,
        "manual_review_required": True,
        "outside_action": blocked,
    }
    (out / "run-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
