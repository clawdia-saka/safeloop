from safeloop import (
    ActionEnvelope,
    ApprovalDecision,
    ApprovalHookRegistry,
    CompensationHookRegistry,
    EffectClass,
    LocalJournalStorage,
    RunViewer,
    Runtime,
    create_app,
)


def test_smoke_imports() -> None:
    assert EffectClass.__name__ == "EffectClass"
    assert ActionEnvelope.__name__ == "ActionEnvelope"
    assert ApprovalDecision.__name__ == "ApprovalDecision"
    assert ApprovalHookRegistry.__name__ == "ApprovalHookRegistry"
    assert CompensationHookRegistry.__name__ == "CompensationHookRegistry"
    assert LocalJournalStorage.__name__ == "LocalJournalStorage"
    assert Runtime.__name__ == "Runtime"
    assert RunViewer.__name__ == "RunViewer"
    assert create_app.__name__ == "create_app"
