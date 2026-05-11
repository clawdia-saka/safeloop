from __future__ import annotations

from safeloop.dedupe import DedupeObservation, ScenarioDedupeGuard, semantic_fingerprint
from safeloop.delta_audit import build_delta_audit_packet, verify_delta_audit_packet
from safeloop.hooks import (
    ApprovalDecision,
    ApprovalHookRegistry,
    CompensationHookRegistry,
)
from safeloop.runtime import ResumableExecution, RunRecord, Runtime
from safeloop.scorecard import summarize_scores
from safeloop.storage import JournalStorageError, LocalJournalStorage
from safeloop.types import ActionEnvelope, EffectClass


def __getattr__(name: str):
    if name in {"RunViewer", "create_app"}:
        from safeloop.api import RunViewer, create_app

        return {"RunViewer": RunViewer, "create_app": create_app}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ActionEnvelope",
    "ApprovalDecision",
    "DedupeObservation",
    "ApprovalHookRegistry",
    "build_delta_audit_packet",
    "verify_delta_audit_packet",
    "CompensationHookRegistry",
    "create_app",
    "EffectClass",
    "JournalStorageError",
    "LocalJournalStorage",
    "ResumableExecution",
    "RunRecord",
    "Runtime",
    "RunViewer",
    "ScenarioDedupeGuard",
    "semantic_fingerprint",
    "summarize_scores",
]
