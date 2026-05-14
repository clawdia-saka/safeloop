from __future__ import annotations

import socket
from typing import Any

import pytest

from safeloop.compensation_adapters import (
    AdapterCapability,
    CompensationAdapterResult,
    EvidenceRequirement,
    RetryGuidance,
    evaluate_compensation_adapter,
)


def _plan_item() -> dict[str, Any]:
    return {
        "side_effect_id": "se-1",
        "effect_class": "github_pr",
        "external_ref": {"url": "https://example.invalid/pr/1"},
        "exact_rollback": False,
        "compensation": {"capability": "manual"},
    }


def test_default_adapter_result_is_interface_only_and_requires_review() -> None:
    result = CompensationAdapterResult.for_plan_item(_plan_item())

    assert result.schema_version == "compensation-adapter-result.v1"
    assert result.exact_rollback is False
    assert result.performed_external_call is False
    assert result.network_calls is False
    assert result.status == "manual_review_required"
    assert result.capability == AdapterCapability.MANUAL
    assert result.evidence_requirements
    assert result.requires_manual_review is True
    assert result.to_dict()["exact_rollback"] is False


def test_adapter_result_exposes_idempotency_and_retry_guidance() -> None:
    result = CompensationAdapterResult.for_plan_item(
        _plan_item(),
        idempotency_key="run-1:se-1:adapter-v1",
        retry_guidance=RetryGuidance(max_attempts=0, retryable=False, backoff_seconds=0, notes="operator only"),
    )

    payload = result.to_dict()
    assert payload["idempotency_key"] == "run-1:se-1:adapter-v1"
    assert payload["retry_guidance"] == {
        "retryable": False,
        "max_attempts": 0,
        "backoff_seconds": 0,
        "notes": "operator only",
    }


def test_result_rejects_exact_rollback_and_missing_evidence() -> None:
    with pytest.raises(ValueError, match="exact_rollback"):
        CompensationAdapterResult(
            adapter_name="demo",
            effect_id="se-1",
            effect_class="github_pr",
            capability=AdapterCapability.VERIFIED,
            status="ready_for_operator",
            exact_rollback=True,
            evidence_requirements=[EvidenceRequirement(kind="operator_confirmation", description="confirm")],
        )

    with pytest.raises(ValueError, match="evidence"):
        CompensationAdapterResult(
            adapter_name="demo",
            effect_id="se-1",
            effect_class="github_pr",
            capability=AdapterCapability.MANUAL,
            status="manual_review_required",
            evidence_requirements=[],
        )


def test_evaluate_compensation_adapter_does_not_perform_network_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_network(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("network calls are not allowed")

    monkeypatch.setattr(socket, "create_connection", fail_network)

    result = evaluate_compensation_adapter(_plan_item(), adapter_name="interface-only")

    assert result.network_calls is False
    assert result.performed_external_call is False
    assert result.exact_rollback is False
    assert result.requires_manual_review is True
