from safeloop.hooks import (
    ApprovalDecision,
    ApprovalHookRegistry,
    CompensationHookRegistry,
)
from safeloop.types import ActionEnvelope, EffectClass


def make_action(effect: EffectClass) -> ActionEnvelope:
    return ActionEnvelope(
        name="apply_change",
        target="/tmp/resource",
        args={"value": "next"},
        diff="-old\n+next",
        actor="agent",
        privileges=["fs.write"],
        idempotency_key="hook-test",
        effect=effect,
    )


def test_approval_hook_registry_returns_allow_block_or_escalate() -> None:
    action = make_action(EffectClass.REVERSIBLE_WRITE)
    decisions = [
        ApprovalDecision.ALLOW,
        ApprovalDecision.BLOCK,
        ApprovalDecision.ESCALATE,
    ]

    for decision in decisions:
        registry = ApprovalHookRegistry()
        registry.register(lambda envelope, selected=decision: selected)

        assert registry.evaluate(action) is decision


def test_compensation_hook_registry_runs_for_compensatable_actions() -> None:
    compensatable_action = make_action(EffectClass.COMPENSATABLE_WRITE)
    irreversible_action = make_action(EffectClass.IRREVERSIBLE_WRITE)
    calls: list[tuple[str, str]] = []

    registry = CompensationHookRegistry()
    registry.register(lambda envelope, error: calls.append((envelope.name, str(error))))

    registry.run(compensatable_action, RuntimeError("boom"))

    assert calls == [("apply_change", "boom")]
    assert registry.run(irreversible_action, RuntimeError("skip")) == []
