from safeloop import ActionEnvelope, EffectClass, Runtime


def test_smoke_imports() -> None:
    assert EffectClass.__name__ == "EffectClass"
    assert ActionEnvelope.__name__ == "ActionEnvelope"
    assert Runtime.__name__ == "Runtime"
