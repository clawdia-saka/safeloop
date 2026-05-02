"""HMAC signing helpers for SafeLoop approval records."""

from __future__ import annotations

import hmac
import hashlib
import json

from safeloop.control_plane.registry import ApprovalRecord

_SIGNED_FIELDS = ("approval_id", "action", "subject", "status", "requested_by", "created_at")


def canonical_approval_payload(record: ApprovalRecord) -> str:
    """Return deterministic JSON for the approval fields covered by HMAC."""
    payload = {field: getattr(record, field) for field in _SIGNED_FIELDS}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sign_approval_record(record: ApprovalRecord, key: bytes) -> ApprovalRecord:
    payload = canonical_approval_payload(record)
    digest = hmac.new(key, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return ApprovalRecord(
        approval_id=record.approval_id,
        requested_by=record.requested_by,
        action=record.action,
        subject=record.subject,
        status=record.status,
        signed_payload=payload,
        signature=f"sha256={digest}",
        created_at=record.created_at,
    )


def verify_approval_record(record: ApprovalRecord, key: bytes) -> bool:
    if not record.signature.startswith("sha256="):
        return False
    expected_payload = canonical_approval_payload(record)
    if not hmac.compare_digest(record.signed_payload, expected_payload):
        return False
    expected = sign_approval_record(record, key).signature
    return hmac.compare_digest(record.signature, expected)
