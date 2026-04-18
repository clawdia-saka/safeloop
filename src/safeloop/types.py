from enum import Enum

from pydantic import BaseModel


class EffectClass(str, Enum):
    READ_ONLY = "read_only"
    REVERSIBLE_WRITE = "reversible_write"
    COMPENSATABLE_WRITE = "compensatable_write"
    IRREVERSIBLE_WRITE = "irreversible_write"


class ActionEnvelope(BaseModel):
    name: str
    target: str
    args: dict[str, object]
    diff: str
    actor: str
    privileges: list[str]
    idempotency_key: str
    effect: EffectClass
