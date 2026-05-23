from __future__ import annotations

from safeloop.action_span import action_span
from safeloop.dedupe import DedupeObservation, ScenarioDedupeGuard, semantic_fingerprint
from safeloop.delta_audit import build_delta_audit_packet, verify_delta_audit_packet
from safeloop.hooks import (
    ApprovalDecision,
    ApprovalHookRegistry,
    CompensationHookRegistry,
)
from safeloop.runtime import ResumableExecution, RunRecord, Runtime
from safeloop.runtime_tool_exec import RuntimeToolExecError, execute_tool_request
from safeloop.runtime_tool_firewall import RuntimeToolFirewallError, firewall_preflight
from safeloop.scorecard import summarize_scores
from safeloop.storage import JournalStorageError, LocalJournalStorage
from safeloop.types import ActionEnvelope, EffectClass


def __getattr__(name: str):
    if name == "RunViewer":
        from safeloop.viewer import RunViewer

        return RunViewer
    if name == "create_app":
        from safeloop.api import create_app

        return create_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ActionEnvelope",
    "action_span",
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
    "RuntimeToolExecError",
    "RuntimeToolFirewallError",
    "RunViewer",
    "ScenarioDedupeGuard",
    "firewall_preflight",
    "execute_tool_request",
    "semantic_fingerprint",
    "summarize_scores",
]
