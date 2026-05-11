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


def test_approval_hook_registry_short_circuits_on_first_non_allow() -> None:
    action = make_action(EffectClass.REVERSIBLE_WRITE)
    calls: list[str] = []

    registry = ApprovalHookRegistry()
    registry.register(lambda envelope: calls.append("first") or ApprovalDecision.ALLOW)
    registry.register(lambda envelope: calls.append("second") or ApprovalDecision.BLOCK)
    registry.register(lambda envelope: calls.append("third") or ApprovalDecision.ESCALATE)

    assert registry.evaluate(action) is ApprovalDecision.BLOCK
    assert calls == ["first", "second"]


def test_approval_hook_registry_normalizes_string_decisions_at_boundary() -> None:
    action = make_action(EffectClass.REVERSIBLE_WRITE)
    registry = ApprovalHookRegistry()
    registry.register(lambda envelope: "block")  # type: ignore[return-value]

    assert registry.evaluate(action) is ApprovalDecision.BLOCK


def test_approval_hook_registry_rejects_unknown_decision_fail_closed() -> None:
    action = make_action(EffectClass.REVERSIBLE_WRITE)
    registry = ApprovalHookRegistry()
    registry.register(lambda envelope: "approve")  # type: ignore[return-value]

    try:
        registry.evaluate(action)
    except ValueError as exc:
        assert "approve" in str(exc)
    else:
        raise AssertionError("unknown hook decision should not execute as allow")


def test_compensation_hook_registry_runs_for_compensatable_actions() -> None:
    compensatable_action = make_action(EffectClass.COMPENSATABLE_WRITE)
    irreversible_action = make_action(EffectClass.IRREVERSIBLE_WRITE)
    calls: list[tuple[str, str]] = []

    registry = CompensationHookRegistry()
    registry.register(lambda envelope, error: calls.append((envelope.name, str(error))))

    registry.run(compensatable_action, RuntimeError("boom"))

    assert calls == [("apply_change", "boom")]
    assert registry.run(irreversible_action, RuntimeError("skip")) is None


def test_compensation_hook_registry_runs_multiple_hooks_for_compensatable_actions() -> None:
    action = make_action(EffectClass.COMPENSATABLE_WRITE)
    calls: list[str] = []

    registry = CompensationHookRegistry()
    registry.register(lambda envelope, error: calls.append(f"first:{error}"))
    registry.register(lambda envelope, error: calls.append(f"second:{envelope.name}"))

    assert registry.run(action, RuntimeError("boom")) is None
    assert calls == ["first:boom", "second:apply_change"]
