"""The list of tools the model may ask for.

A tool either runs straight away (`run`) or only proposes (`propose`). Proposed
tools go through the safety gate, and no tool can decide a proposal. A `quick`
proposal skips the gate when the owner asked for it directly and nothing about it
looks sensitive. Sending to other people and forgetting are never quick.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

STRING = "STRING"
INTEGER = "INTEGER"
BOOLEAN = "BOOLEAN"


# Anything touching money, secrets, deletion, system control or other people keeps CONFIRM.
# So does an open document, because typing replaces whatever is selected in it.
SENSITIVE = re.compile(
    r"\b(password|passcode|otp|pin|cvv|credit card|bank|banking|paypal|payment|pay|purchase|checkout|buy|order"
    r"|transfer|wallet|crypto|delete|remove|erase|format|uninstall|shutdown|shut down|restart"
    r"|powershell|cmd|command prompt|terminal|regedit|registry|administrator|sign in|log in|login"
    r"|win|send|post|publish|submit|confirm|whatsapp|telegram|discord|slack|teams|kakaotalk|gmail|outlook"
    r"|mail|inbox|instagram|messenger)\b"
    r"|- (word|excel|powerpoint)\b"
    r"|\w\.(py|txt|md|docx?|xlsx?|pptx?|csv|json|js|ts|html?|pdf|log|ini|ya?ml)\b",
    re.IGNORECASE,
)


class ToolRefused(ValueError):
    """The request was unacceptable, or the computer cannot do it."""


@dataclass(frozen=True)
class Plan:
    """What a proposed tool would do, shown on the CONFIRM card before anything happens."""

    summary: str
    where: str
    preview: str
    perform: Callable[[], str]
    plain: bool = False  # Its text is only file names, which say nothing about what the action does.

    @property
    def is_sensitive(self) -> bool:
        return not self.plain and SENSITIVE.search(f"{self.summary}\n{self.preview}") is not None


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict = field(default_factory=dict)
    required: tuple = ()
    run: Callable[[dict], dict] | None = None
    propose: Callable[[dict], Plan] | None = None
    quick: bool = False
    reads_outside: bool = False

    @property
    def needs_confirmation(self) -> bool:
        return self.propose is not None

    def declaration(self) -> dict:
        # Instant tools run in order. Proposals wait for a person, so the model may talk meanwhile.
        behaviour = "NON_BLOCKING" if self.needs_confirmation else "BLOCKING"
        declared = {"name": self.name, "description": self.description, "behavior": behaviour}
        if self.parameters:
            declared["parameters"] = {
                "type": "OBJECT",
                "properties": {
                    name: {"type": kind, "description": text} for name, (kind, text) in self.parameters.items()
                },
                "required": list(self.required),
            }
        return declared


class ToolBox:
    def __init__(self, tools: list[Tool], undo: UndoStack | None = None, board=None, reminders=None):
        self._tools = {tool.name: tool for tool in tools}
        self.undo = undo or UndoStack()
        self.board = board
        self.reminders = reminders

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    @property
    def names(self) -> list[str]:
        return list(self._tools)

    def declarations(self) -> list[dict]:
        return [{"function_declarations": [tool.declaration() for tool in self._tools.values()]}]


class UndoStack:
    """The most recent reversible actions, newest last. Safe from the tools thread only."""

    LONGEST = 20

    def __init__(self):
        self._steps: list[tuple[str, Callable[[], None]]] = []

    def push(self, label: str, reverse: Callable[[], None]) -> None:
        self._steps = [*self._steps, (label, reverse)][-self.LONGEST :]

    def undo(self) -> str:
        if not self._steps:
            raise ToolRefused("There is nothing to undo.")
        label, reverse = self._steps.pop()
        reverse()
        return f"Undid it: {label}."


def whole_number(arguments: dict, name: str, low: int, high: int) -> int:
    value = arguments.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != int(value):
        raise ToolRefused(f"{name} must be a whole number.")
    value = int(value)
    if not low <= value <= high:
        raise ToolRefused(f"{name} must be between {low} and {high}.")
    return value


def text(arguments: dict, name: str, longest: int = 2_000) -> str:
    value = arguments.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ToolRefused(f"{name} must be some text.")
    if len(value) > longest:
        raise ToolRefused(f"{name} is too long, the limit is {longest} characters.")
    return value
