from __future__ import annotations

import importlib.util
import json
import socket
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO_PATH = ROOT / "examples" / "fake_external_webhook_compensation_demo.py"
_spec = importlib.util.spec_from_file_location("fake_external_webhook_compensation_demo", DEMO_PATH)
assert _spec is not None and _spec.loader is not None
_demo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_demo)
run_demo = _demo.run_demo


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_fake_external_webhook_compensation_demo_is_offline_and_separates_boundaries(tmp_path, monkeypatch) -> None:
    def deny_network(*args, **kwargs):  # pragma: no cover - should never be called
        raise AssertionError("fake webhook demo must not open network sockets")

    monkeypatch.setattr(socket, "create_connection", deny_network)
    monkeypatch.setattr(socket.socket, "connect", deny_network)

    run_dir = run_demo(tmp_path / "fake-webhook-run")

    effects_path = run_dir / "external-effects.jsonl"
    packet_path = run_dir / "operator-packet.md"
    compensation_path = run_dir / "manual-compensation-result.json"

    assert effects_path.exists()
    effects = _jsonl(effects_path)
    assert len(effects) == 1
    effect = effects[0]
    assert effect["effect_class"] == "fake_local_webhook_delivery"
    assert effect["transport"] == "local_file_fixture"
    assert effect["fake_local_only"] is True
    assert effect["exact_rollback"] is False
    assert effect["manual_review_required"] is True
    assert effect["evidence_required"] is True
    assert "example.invalid" in effect["fake_webhook_url"]
    assert effect["network_dispatched"] is False

    result = json.loads(compensation_path.read_text(encoding="utf-8"))
    assert result["compensation"]["capability"] == "manual"
    assert result["compensation"]["exact_rollback"] is False
    assert result["compensation"]["evidence_required"] is True
    assert result["compensation"]["status"] == "fake_artifact_recorded_for_operator_review"

    packet = packet_path.read_text(encoding="utf-8")
    assert "FAKE/LOCAL ONLY" in packet
    assert "No third-party systems are contacted" in packet
    assert "Local rollback" in packet
    assert "External manual review" in packet
    assert "not exact rollback" in packet


def test_docs_note_discloses_fake_webhook_demo_is_local_only() -> None:
    text = (ROOT / "docs" / "compensation.md").read_text(encoding="utf-8")
    assert "fake external webhook compensation demo" in text
    assert "fully fake/local/offline" in text
    assert "does not contact third-party systems" in text
    assert "exact_rollback: false" in text
