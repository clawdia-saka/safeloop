from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from safeloop.side_effect_ledger import (
    LocalSideEffectLedger,
    PreparedSideEffect,
    SideEffectAdapterIdentity,
    SideEffectRecord,
)


def read_events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def event_hash(event: dict) -> str:
    payload = dict(event)
    payload.pop("event_hash", None)
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def test_side_effect_ledger_records_strict_redacted_intent(tmp_path: Path) -> None:
    ledger = LocalSideEffectLedger(tmp_path / "side-effects.jsonl", run_id="run-005")

    event = ledger.append(
        SideEffectRecord(
            phase="intent",
            effect_class="http",
            adapter=SideEffectAdapterIdentity(name="mock-http", version="test"),
            target={"url": "https://api.example.test/v1/messages", "authorization": "Bearer secret-token"},
            reason="operator_requested_external_call",
            raw_payload={"body": "send token=secret-token to alice@example.com"},
        )
    )

    events = read_events(tmp_path / "side-effects.jsonl")
    assert events == [event]
    assert event["schema_version"] == "side-effect-ledger.v1"
    assert event["run_id"] == "run-005"
    assert event["phase"] == "intent"
    assert event["privacy"] == {"redaction": "strict", "contains_secret": False, "raw_payload_persisted": False}
    serialized = json.dumps(event, sort_keys=True)
    assert "secret-token" not in serialized
    assert "authorization" not in serialized.lower()
    assert "raw_payload" not in event
    assert event["target"] == {"url": "https://api.example.test/v1/messages"}


def test_compensation_capability_is_explicit(tmp_path: Path) -> None:
    ledger = LocalSideEffectLedger(tmp_path / "side-effects.jsonl", run_id="run-005")

    event = ledger.append(
        SideEffectRecord(
            phase="committed",
            effect_class="git",
            adapter=SideEffectAdapterIdentity(name="mock-git", version="test"),
            target={"repo": "nous/safeloop"},
            reason="local_mock_commit",
            external_ref="abc123def456",
            idempotency_key="run-005:commit:abc123def456",
            compensation={"capability": "best_effort", "operator_notes": "Revert commit abc123def456 if needed."},
        )
    )

    assert event["compensation"]["capability"] == "best_effort"
    assert "operator_notes" in event["compensation"]
    none_event = ledger.append(
        SideEffectRecord(
            phase="observed",
            effect_class="git",
            adapter=SideEffectAdapterIdentity(name="mock-git", version="test"),
            target={"repo": "nous/safeloop"},
            reason="default_no_compensation_available",
        )
    )
    assert none_event["compensation"] == {"capability": "none"}
    with pytest.raises(ValueError, match="compensation.capability"):
        ledger.append(
            SideEffectRecord(
                phase="observed",
                effect_class="git",
                adapter=SideEffectAdapterIdentity(name="mock-git", version="test"),
                target={"repo": "nous/safeloop"},
                reason="invalid_compensation_contract",
                compensation={"capability": "automatic"},
            )
        )


def test_committed_event_requires_adapter_identity_and_idempotency(tmp_path: Path) -> None:
    ledger = LocalSideEffectLedger(tmp_path / "side-effects.jsonl", run_id="run-005")

    with pytest.raises(ValueError, match="adapter.name"):
        ledger.append(
            SideEffectRecord(
                phase="committed",
                effect_class="email",
                adapter=SideEffectAdapterIdentity(name="", version="test"),
                target={"to": "u***@example.test"},
                reason="missing_adapter_name",
                external_ref="msg-1",
                idempotency_key="email:msg-1",
            )
        )

    with pytest.raises(ValueError, match="idempotency_key"):
        ledger.append(
            SideEffectRecord(
                phase="committed",
                effect_class="email",
                adapter=SideEffectAdapterIdentity(name="mock-email", version="test", supports_idempotency=True),
                target={"to": "u***@example.test"},
                reason="missing_idempotency_key",
                external_ref="msg-1",
            )
        )

    event = ledger.append(
        SideEffectRecord(
            phase="committed",
            effect_class="email",
            adapter=SideEffectAdapterIdentity(name="mock-email", version="test", supports_idempotency=True),
            target={"to": "u***@example.test"},
            reason="mock_send",
            external_ref="msg-1",
            idempotency_key="email:msg-1",
        )
    )
    assert event["adapter"] == {"name": "mock-email", "version": "test", "supports_idempotency": True}
    assert event["idempotency_key"] == "email:msg-1"
    assert event["external_ref"] == "msg-1"


def test_camel_case_sensitive_keys_are_redacted(tmp_path: Path) -> None:
    ledger = LocalSideEffectLedger(tmp_path / "side-effects.jsonl", run_id="run-005")

    event = ledger.append(
        SideEffectRecord(
            phase="intent",
            effect_class="http",
            adapter=SideEffectAdapterIdentity(name="mock-http", version="test"),
            target={"apiKey": "secret", "accessToken": "token", "nested": {"refreshToken": "token", "ok": "yes"}},
            reason="camel_case_redaction",
        )
    )

    assert event["target"] == {"nested": {"ok": "yes"}}


def test_adapter_prepare_does_not_imply_commit(tmp_path: Path) -> None:
    ledger = LocalSideEffectLedger(tmp_path / "side-effects.jsonl", run_id="run-005")
    prepared = PreparedSideEffect(
        adapter=SideEffectAdapterIdentity(name="mock-deploy", version="test", supports_idempotency=True),
        effect_class="deploy",
        target={"service": "preview-app"},
        idempotency_key="deploy:preview-app:run-005",
        reason="validated_deploy_target",
        compensation={"capability": "manual", "operator_notes": "Cancel deployment in provider UI."},
    )

    event = ledger.record_prepared(prepared)

    assert event["phase"] == "prepared"
    assert event["external_ref"] is None
    assert read_events(tmp_path / "side-effects.jsonl")[0]["phase"] == "prepared"
    assert all(e["phase"] != "committed" for e in read_events(tmp_path / "side-effects.jsonl"))


def test_side_effect_ledger_events_are_hash_chained(tmp_path: Path) -> None:
    ledger = LocalSideEffectLedger(tmp_path / "side-effects.jsonl", run_id="run-005")

    first = ledger.append(SideEffectRecord(phase="intent", effect_class="file", target={"path": "a"}))
    second = ledger.append(SideEffectRecord(phase="prepared", effect_class="file", target={"path": "b"}))

    assert first["prev_event_hash"] is None
    assert isinstance(first["event_hash"], str) and len(first["event_hash"]) == 64
    assert second["prev_event_hash"] == first["event_hash"]
    assert second["event_hash"] != first["event_hash"]
    persisted = read_events(tmp_path / "side-effects.jsonl")
    assert persisted[1]["prev_event_hash"] == persisted[0]["event_hash"]


def test_side_effect_ledger_rejects_appending_to_legacy_unhashed_history(tmp_path: Path) -> None:
    path = tmp_path / "side-effects.jsonl"
    path.write_text(json.dumps({"schema_version": "side-effect-ledger.v1", "phase": "intent"}) + "\n", encoding="utf-8")
    ledger = LocalSideEffectLedger(path, run_id="run-005")

    try:
        ledger.append(SideEffectRecord(phase="prepared", effect_class="file", target={"path": "b"}))
    except ValueError as exc:
        assert "cannot append to unhashed legacy side-effect ledger" in str(exc)
    else:
        raise AssertionError("expected legacy append rejection")


def test_side_effect_ledger_rejects_appending_to_corrupted_hash_chain(tmp_path: Path) -> None:
    path = tmp_path / "side-effects.jsonl"
    ledger = LocalSideEffectLedger(path, run_id="run-005")
    first = ledger.append(SideEffectRecord(phase="intent", effect_class="file", target={"path": "a"}))
    second = ledger.append(SideEffectRecord(phase="prepared", effect_class="file", target={"path": "b"}))
    first["target"] = {"path": "tampered"}
    path.write_text(json.dumps(first) + "\n" + json.dumps(second) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="cannot append to corrupted side-effect ledger"):
        ledger.append(SideEffectRecord(phase="committed", effect_class="file", target={"path": "c"}))


def test_side_effect_ledger_rejects_appending_after_prev_hash_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "side-effects.jsonl"
    ledger = LocalSideEffectLedger(path, run_id="run-005")
    first = ledger.append(SideEffectRecord(phase="intent", effect_class="file", target={"path": "a"}))
    second = ledger.append(SideEffectRecord(phase="prepared", effect_class="file", target={"path": "b"}))
    second["prev_event_hash"] = "0" * 64
    second["event_hash"] = event_hash(second)
    path.write_text(json.dumps(first) + "\n" + json.dumps(second) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="cannot append to corrupted side-effect ledger"):
        ledger.append(SideEffectRecord(phase="committed", effect_class="file", target={"path": "c"}))
