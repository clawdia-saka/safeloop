#!/usr/bin/env python3
"""Deterministic fake/local external webhook compensation demo.

This example intentionally performs no network I/O.  It writes a local JSONL
record that represents a fake external webhook delivery, then writes a manual
compensation artifact and an operator packet that separates local rollback from
external manual review.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


FAKE_WEBHOOK_URL = "https://example.invalid/safeloop/fake-local-webhook"
RUN_ID = "fake-webhook-demo-0001"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def run_demo(output_dir: str | Path | None = None) -> Path:
    """Run the fake webhook demo and return the artifact directory.

    The demo is fully fake/local/offline: it never imports HTTP clients, never
    opens sockets, and never dispatches to the fake URL.  The URL uses
    ``example.invalid`` only as a visible non-routable label in the artifact.
    """

    run_dir = Path(output_dir) if output_dir is not None else Path(tempfile.mkdtemp(prefix="safeloop-fake-webhook-"))
    run_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime(2026, 1, 14, 12, 0, 0, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    fake_payload = {
        "event": "invoice.updated",
        "invoice_id": "inv_fake_114",
        "amount_cents": 11400,
        "demo": "fake_external_webhook_compensation",
    }

    effect = {
        "schema_version": "external-effect-local-demo.v1",
        "run_id": RUN_ID,
        "recorded_at": timestamp,
        "effect_class": "fake_local_webhook_delivery",
        "adapter": {"name": "fake-local-webhook", "version": "demo.1", "supports_idempotency": True},
        "transport": "local_file_fixture",
        "fake_local_only": True,
        "fake_webhook_url": FAKE_WEBHOOK_URL,
        "network_dispatched": False,
        "external_ref": "local-file://external-effects.jsonl#fake-webhook-demo-0001",
        "idempotency_key": "fake-webhook-demo-0001",
        "payload_summary": fake_payload,
        "exact_rollback": False,
        "manual_review_required": True,
        "evidence_required": True,
        "warning": "This fake external effect cannot be exactly rolled back; operator evidence is required.",
    }
    _append_jsonl(run_dir / "external-effects.jsonl", effect)

    result = {
        "schema_version": "manual-compensation-result.v1",
        "run_id": RUN_ID,
        "fake_local_only": True,
        "source_effect": effect["external_ref"],
        "compensation": {
            "capability": "manual",
            "action": "operator_review_fake_webhook_and_record_evidence",
            "status": "fake_artifact_recorded_for_operator_review",
            "exact_rollback": False,
            "evidence_required": True,
            "evidence_placeholder": "Attach a human review note; no external system was contacted.",
        },
    }
    _write_json(run_dir / "manual-compensation-result.json", result)

    packet = "\n".join(
        [
            "# Fake external webhook compensation operator packet",
            "",
            "**FAKE/LOCAL ONLY**: No third-party systems are contacted; no webhook is dispatched.",
            "",
            "## Local rollback",
            "- Covered local files may be reverted by normal SafeLoop/local rollback workflows.",
            "- This demo does not change local rollback behavior.",
            "",
            "## External manual review",
            "- Fake external effect: fake_local_webhook_delivery recorded in external-effects.jsonl.",
            "- Compensation is manual review only and is not exact rollback.",
            "- Evidence required: operator must attach a review note before closing the packet.",
            "",
        ]
    )
    (run_dir / "operator-packet.md").write_text(packet, encoding="utf-8")
    return run_dir


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args()
    run_dir = run_demo(args.output_dir)
    print(run_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
