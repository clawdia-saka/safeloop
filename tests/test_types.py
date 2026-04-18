from pydantic import ValidationError
import pytest

from safeloop.types import ActionEnvelope, EffectClass


def test_effect_class_enum_values() -> None:
    assert EffectClass.READ_ONLY.value == "read_only"
    assert EffectClass.REVERSIBLE_WRITE.value == "reversible_write"
    assert EffectClass.COMPENSATABLE_WRITE.value == "compensatable_write"
    assert EffectClass.IRREVERSIBLE_WRITE.value == "irreversible_write"


def test_action_envelope_fields_round_trip() -> None:
    envelope = ActionEnvelope(
        name="create_file",
        target="/tmp/example.txt",
        args={"content": "hello"},
        diff="+hello",
        actor="agent",
        privileges=["fs.write"],
        idempotency_key="op-123",
        effect=EffectClass.REVERSIBLE_WRITE,
    )

    assert envelope.name == "create_file"
    assert envelope.target == "/tmp/example.txt"
    assert envelope.args == {"content": "hello"}
    assert envelope.diff == "+hello"
    assert envelope.actor == "agent"
    assert envelope.privileges == ["fs.write"]
    assert envelope.idempotency_key == "op-123"
    assert envelope.effect == EffectClass.REVERSIBLE_WRITE


def test_action_envelope_requires_all_fields() -> None:
    with pytest.raises(ValidationError) as excinfo:
        ActionEnvelope()

    errors = {error["loc"][0] for error in excinfo.value.errors()}
    assert errors == {
        "name",
        "target",
        "args",
        "diff",
        "actor",
        "privileges",
        "idempotency_key",
        "effect",
    }
