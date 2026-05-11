"""SafeLoop 0.0.5 local side-effect ledger runtime primitives.

This module is intentionally local-only: it writes append-only JSONL records for
already-observed or intended side effects and provides a small adapter identity
contract for tests/examples. It does not perform external writes.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from safeloop.storage import exclusive_lock

SCHEMA_VERSION = "side-effect-ledger.v1"
_ALLOWED_PHASES = {"intent", "prepared", "committed", "observed", "compensating", "compensated", "failed", "redacted"}
_ALLOWED_EFFECT_CLASSES = {"file", "git", "http", "email", "chat", "payment", "deploy", "unknown"}
_ALLOWED_COMPENSATION = {"none", "manual", "best_effort", "verified"}
_SENSITIVE_KEYS = {
    "authorization",
    "access_token",
    "api_key",
    "body",
    "cookie",
    "password",
    "payload",
    "raw",
    "request",
    "response",
    "secret",
    "token",
}
_SECRET_VALUE_PATTERN = re.compile(r"(?i)(bearer\s+[a-z0-9._~+/=-]+|token\s*[=:]\s*[^\s,;]+|secret[-_a-z0-9]*|sk-[a-z0-9]{8,})")


@dataclass(frozen=True)
class SideEffectAdapterIdentity:
    """Adapter identity persisted on each side-effect event."""

    name: str
    version: str
    supports_idempotency: bool = False

    def to_record(self) -> dict[str, str | bool]:
        if not self.name:
            raise ValueError("adapter.name is required")
        if not self.version:
            raise ValueError("adapter.version is required")
        return {"name": self.name, "version": self.version, "supports_idempotency": self.supports_idempotency}


@dataclass(frozen=True)
class SideEffectRecord:
    phase: str
    effect_class: str
    adapter: SideEffectAdapterIdentity = field(default_factory=lambda: SideEffectAdapterIdentity("local", "builtin"))
    target: dict[str, Any] = field(default_factory=dict)
    reason: str = "unspecified"
    idempotency_key: str | None = None
    external_ref: str | None = None
    privacy: dict[str, Any] = field(default_factory=dict)
    compensation: dict[str, Any] = field(default_factory=dict)
    raw_payload: Any | None = None


@dataclass(frozen=True)
class PreparedSideEffect:
    adapter: SideEffectAdapterIdentity
    effect_class: str
    target: dict[str, Any]
    reason: str
    idempotency_key: str | None = None
    compensation: dict[str, Any] = field(default_factory=dict)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return _SECRET_VALUE_PATTERN.sub("[REDACTED]", value)
    if isinstance(value, dict):
        return _redact_target(value)
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    return value


def _sensitive_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", key.lower())
    sensitive = {re.sub(r"[^a-z0-9]", "", item) for item in _SENSITIVE_KEYS}
    return normalized in sensitive or normalized.endswith(("token", "key", "secret", "password"))


def _redact_target(target: dict[str, Any]) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for key, value in target.items():
        if _sensitive_key(key):
            continue
        redacted[key] = _redact_value(value)
    return redacted


def _compensation(record: SideEffectRecord) -> dict[str, Any]:
    comp = {"capability": "none", **record.compensation}
    capability = comp.get("capability")
    if capability not in _ALLOWED_COMPENSATION:
        raise ValueError("compensation.capability must be one of none, manual, best_effort, verified")
    return comp


class LocalSideEffectLedger:
    """Append-only writer for a run-local ``side-effects.jsonl`` file."""

    def __init__(self, path: Path, run_id: str):
        if not run_id:
            raise ValueError("run_id is required")
        self.path = Path(path)
        self.run_id = run_id
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)

    def append(self, record: SideEffectRecord) -> dict[str, Any]:
        event = self._event(record)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        with lock_path.open("a+", encoding="utf-8") as lock_handle:
            with exclusive_lock(lock_handle):
                previous_hash = _latest_event_hash(self.path)
                event["prev_event_hash"] = previous_hash
                event["event_hash"] = _event_hash(event)
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")
        return event

    def record_prepared(self, prepared: PreparedSideEffect) -> dict[str, Any]:
        return self.append(
            SideEffectRecord(
                phase="prepared",
                effect_class=prepared.effect_class,
                adapter=prepared.adapter,
                target=prepared.target,
                reason=prepared.reason,
                idempotency_key=prepared.idempotency_key,
                compensation=prepared.compensation,
            )
        )

    def _event(self, record: SideEffectRecord) -> dict[str, Any]:
        if record.phase not in _ALLOWED_PHASES:
            raise ValueError("phase is not allowed")
        if record.effect_class not in _ALLOWED_EFFECT_CLASSES:
            raise ValueError("effect_class is not allowed")
        adapter = record.adapter.to_record()
        if record.adapter.supports_idempotency and record.phase in {"committed", "observed", "compensated"} and not record.idempotency_key:
            raise ValueError("idempotency_key is required for commit-capable adapters")
        privacy = {"redaction": "strict", "contains_secret": False, "raw_payload_persisted": False, **record.privacy}
        if privacy.get("redaction") != "strict" or privacy.get("contains_secret") is not False:
            raise ValueError("persisted side-effect records require strict redaction and contains_secret=false")
        event = {
            "schema_version": SCHEMA_VERSION,
            "event_id": "sevt-" + uuid.uuid4().hex,
            "run_id": self.run_id,
            "created_at": utc_now(),
            "phase": record.phase,
            "effect_class": record.effect_class,
            "adapter": adapter,
            "target": _redact_target(record.target),
            "idempotency_key": record.idempotency_key,
            "external_ref": _redact_value(record.external_ref) if record.external_ref is not None else None,
            "privacy": privacy,
            "compensation": _compensation(record),
            "reason": record.reason,
        }
        for value in _string_values(event):
            if _SECRET_VALUE_PATTERN.search(value):
                raise ValueError("record still appears to contain secret-like data after redaction")
        return event


def _string_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        values: list[str] = []
        for item in value.values():
            values.extend(_string_values(item))
        return values
    if isinstance(value, list):
        values = []
        for item in value:
            values.extend(_string_values(item))
        return values
    return []


def _event_hash(event: dict[str, Any]) -> str:
    payload = dict(event)
    payload.pop("event_hash", None)
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _latest_event_hash(path: Path) -> str | None:
    previous: str | None = None
    for line_number, event in enumerate(read_side_effect_events(path), start=1):
        hash_value = event.get("event_hash")
        if not isinstance(hash_value, str) or not _is_hex_sha256(hash_value):
            raise ValueError("cannot append to unhashed legacy side-effect ledger")
        if event.get("prev_event_hash") != previous:
            raise ValueError(f"cannot append to corrupted side-effect ledger: prev hash mismatch at line {line_number}")
        if _event_hash(event) != hash_value:
            raise ValueError(f"cannot append to corrupted side-effect ledger: event hash mismatch at line {line_number}")
        previous = hash_value
    return previous


def _is_hex_sha256(value: str) -> bool:
    if len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def read_side_effect_events(path: Path) -> list[dict[str, Any]]:
    if not Path(path).exists():
        return []
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
