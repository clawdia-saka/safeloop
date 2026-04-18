from dataclasses import dataclass
from enum import Enum


class EffectClass(str, Enum):
    COMMAND = "command"


@dataclass(slots=True)
class ActionEnvelope:
    effect: EffectClass
    payload: dict[str, object]
