"""What can come back from a live session, and the shape of a session.

The rest of ALPHA only sees these types, so it never depends on the details of
the provider's messages.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import AsyncIterator, Protocol, Union


@dataclass(frozen=True)
class ReplyAudio:
    pcm24k: bytes


@dataclass(frozen=True)
class ReplyText:
    text: str


@dataclass(frozen=True)
class HeardText:
    text: str


@dataclass(frozen=True)
class ReplyDone:
    pass


@dataclass(frozen=True)
class ReplyInterrupted:
    pass


@dataclass(frozen=True)
class ToolRequest:
    call_id: str
    name: str
    arguments: dict = field(default_factory=dict)


@dataclass(frozen=True)
class EndingSoon:
    pass


LiveEvent = Union[ReplyAudio, ReplyText, HeardText, ReplyDone, ReplyInterrupted, ToolRequest, EndingSoon]


class LiveSession(Protocol):
    async def send_text(self, text: str) -> None: ...
    async def send_note(self, text: str) -> None: ...
    async def send_image(self, jpeg: bytes) -> None: ...
    async def start_speech(self) -> None: ...
    async def send_speech(self, pcm16k: bytes) -> None: ...
    async def end_speech(self) -> None: ...
    async def send_tool_result(self, call_id: str, name: str, result: dict, later: bool = False) -> None: ...
    def events(self) -> AsyncIterator[LiveEvent]: ...
    async def close(self) -> None: ...
