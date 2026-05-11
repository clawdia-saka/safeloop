"""Extension points for approval and compensation hooks."""

from __future__ import annotations

from enum import Enum
from typing import Protocol

from safeloop.types import ActionEnvelope, EffectClass


class ApprovalDecision(str, Enum):
    ALLOW = "allow"
    BLOCK = "block"
    ESCALATE = "escalate"


class ApprovalHook(Protocol):
    def __call__(self, action: ActionEnvelope) -> ApprovalDecision:
        ...


class CompensationHook(Protocol):
    def __call__(self, action: ActionEnvelope, error: Exception) -> None:
        ...


class ApprovalHookRegistry:
    def __init__(self) -> None:
        self._hooks: list[ApprovalHook] = []

    def register(self, hook: ApprovalHook) -> None:
        self._hooks.append(hook)

    def evaluate(self, action: ActionEnvelope) -> ApprovalDecision:
        decision = ApprovalDecision.ALLOW
        for hook in self._hooks:
            decision = ApprovalDecision(hook(action))
            if decision is not ApprovalDecision.ALLOW:
                return decision
        return decision


class CompensationHookRegistry:
    def __init__(self) -> None:
        self._hooks: list[CompensationHook] = []

    def register(self, hook: CompensationHook) -> None:
        self._hooks.append(hook)

    def run(self, action: ActionEnvelope, error: Exception) -> None:
        if action.effect is not EffectClass.COMPENSATABLE_WRITE:
            return
        for hook in self._hooks:
            hook(action, error)


__all__ = [
    "ApprovalDecision",
    "ApprovalHook",
    "ApprovalHookRegistry",
    "CompensationHook",
    "CompensationHookRegistry",
]
