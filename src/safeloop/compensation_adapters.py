"""Interface-only compensation adapter contracts.

SafeLoop compensation adapters describe whether an external side effect has a
local, operator-reviewable compensation path.  They do not execute remediation,
open sockets, call hosted control planes, or change local rollback behavior.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

SCHEMA_VERSION = "compensation-adapter-result.v1"


class AdapterCapability(StrEnum):
    """Stable capability vocabulary for compensation adapter results."""

    NONE = "none"
    MANUAL = "manual"
    BEST_EFFORT = "best_effort"
    VERIFIED = "verified"


@dataclass(frozen=True)
class EvidenceRequirement:
    """Evidence an operator must collect before marking compensation reviewed."""

    kind: str
    description: str
    required: bool = True

    def __post_init__(self) -> None:
        if not self.kind.strip():
            raise ValueError("evidence kind is required")
        if not self.description.strip():
            raise ValueError("evidence description is required")

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "description": self.description, "required": self.required}


@dataclass(frozen=True)
class RetryGuidance:
    """Retry guidance for a future operator/tooling step, not executed here.

    ``max_attempts=0`` and ``retryable=False`` means SafeLoop will not retry the
    compensation automatically.  Non-zero values are advisory only and should be
    combined with a stable ``idempotency_key`` by any external executor.
    """

    retryable: bool = False
    max_attempts: int = 0
    backoff_seconds: int = 0
    notes: str = "manual operator review only; SafeLoop does not execute retries"

    def __post_init__(self) -> None:
        if self.max_attempts < 0:
            raise ValueError("max_attempts must be non-negative")
        if self.backoff_seconds < 0:
            raise ValueError("backoff_seconds must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "retryable": self.retryable,
            "max_attempts": self.max_attempts,
            "backoff_seconds": self.backoff_seconds,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class CompensationAdapterResult:
    """Local result returned by compensation adapters.

    This is a contract/result type only.  ``exact_rollback`` is always false;
    evidence and manual review are mandatory; network/external execution flags
    are always false for this interface-only slice.
    """

    adapter_name: str
    effect_id: str
    effect_class: str
    capability: AdapterCapability = AdapterCapability.NONE
    status: str = "manual_review_required"
    evidence_requirements: list[EvidenceRequirement] = field(default_factory=list)
    idempotency_key: str | None = None
    retry_guidance: RetryGuidance = field(default_factory=RetryGuidance)
    exact_rollback: bool = False
    requires_manual_review: bool = True
    performed_external_call: bool = False
    network_calls: bool = False
    notes: tuple[str, ...] = ("external effects are mitigation-only, never exact rollback",)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.exact_rollback is not False:
            raise ValueError("exact_rollback must always be false for compensation adapter results")
        if self.performed_external_call or self.network_calls:
            raise ValueError("compensation adapter interface results must not perform network/external calls")
        if not self.requires_manual_review:
            raise ValueError("manual review is required for external compensation")
        if not self.evidence_requirements:
            raise ValueError("at least one evidence requirement is required")
        if not self.adapter_name.strip():
            raise ValueError("adapter_name is required")
        if not self.effect_id.strip():
            raise ValueError("effect_id is required")
        if not self.effect_class.strip():
            raise ValueError("effect_class is required")

    @classmethod
    def for_plan_item(
        cls,
        plan_item: dict[str, Any],
        *,
        adapter_name: str = "interface-only",
        idempotency_key: str | None = None,
        retry_guidance: RetryGuidance | None = None,
    ) -> "CompensationAdapterResult":
        """Build a local adapter result from a compensation plan item."""

        raw_capability = str(plan_item.get("compensation", {}).get("capability") or plan_item.get("compensation_capability") or "none")
        try:
            capability = AdapterCapability(raw_capability)
        except ValueError:
            capability = AdapterCapability.NONE
        effect_id = str(plan_item.get("side_effect_id") or plan_item.get("effect_id") or "unknown")
        effect_class = str(plan_item.get("effect_class") or plan_item.get("kind") or "unknown")
        return cls(
            adapter_name=adapter_name,
            effect_id=effect_id,
            effect_class=effect_class,
            capability=capability,
            status="manual_review_required",
            evidence_requirements=[
                EvidenceRequirement(
                    kind="operator_confirmation",
                    description="Operator must verify the external system state and attach local evidence before closing compensation review.",
                ),
                EvidenceRequirement(
                    kind="idempotency_record",
                    description="Record the idempotency key used by any later external executor to avoid duplicate compensation.",
                ),
            ],
            idempotency_key=idempotency_key or f"{adapter_name}:{effect_id}:v1",
            retry_guidance=retry_guidance or RetryGuidance(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "adapter_name": self.adapter_name,
            "effect_id": self.effect_id,
            "effect_class": self.effect_class,
            "capability": self.capability.value,
            "status": self.status,
            "exact_rollback": False,
            "requires_manual_review": self.requires_manual_review,
            "performed_external_call": False,
            "network_calls": False,
            "evidence_requirements": [item.to_dict() for item in self.evidence_requirements],
            "idempotency_key": self.idempotency_key,
            "retry_guidance": self.retry_guidance.to_dict(),
            "notes": list(self.notes),
        }


@runtime_checkable
class CompensationAdapter(Protocol):
    """Protocol for local-only compensation adapter evaluators."""

    name: str

    def evaluate(self, plan_item: dict[str, Any]) -> CompensationAdapterResult:
        """Return a local result; do not execute external compensation."""
        ...


def evaluate_compensation_adapter(plan_item: dict[str, Any], *, adapter_name: str = "interface-only") -> CompensationAdapterResult:
    """Default deterministic evaluator for the interface-only adapter contract."""

    return CompensationAdapterResult.for_plan_item(plan_item, adapter_name=adapter_name)
