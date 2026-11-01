"""ALPHA's visible states and their default colours."""

from __future__ import annotations

from enum import Enum


class State(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    WARNING = "warning"
    ERROR = "error"

    @property
    def label(self) -> str:
        return self.value.capitalize()


DEFAULT_COLOURS: dict[str, str] = {
    State.IDLE.value: "#2f6bff",
    State.LISTENING.value: "#19e3e3",
    State.THINKING.value: "#8a4dff",
    State.SPEAKING.value: "#1fa2ff",
    State.WARNING.value: "#ffb020",
    State.ERROR.value: "#ff3347",
}
