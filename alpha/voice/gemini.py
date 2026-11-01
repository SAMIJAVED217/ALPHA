"""The Gemini Live connection, through the official Google GenAI SDK.

The SDK is imported inside `connect`, which only ever runs on the conversation
thread, so a slow first import never touches the interface.
"""

from __future__ import annotations

import logging
from typing import AsyncIterator

from .events import (
    EndingSoon,
    HeardText,
    LiveEvent,
    ReplyAudio,
    ReplyDone,
    ReplyInterrupted,
    ReplyText,
    ToolRequest,
)
from .. import languages
from ..characters import CHARACTERS, DEFAULT_CHARACTER
from ..tools.catalogue import declarations
from .persona import SYSTEM_INSTRUCTION

log = logging.getLogger(__name__)

INPUT_MIME = "audio/pcm;rate=16000"


def session_config(character_id: str = DEFAULT_CHARACTER, language: str = "en", about: str = "") -> dict:
    """Automatic speech detection is off because push-to-talk marks each turn itself."""
    character = CHARACTERS[character_id]
    return {
        "response_modalities": ["AUDIO"],
        "system_instruction": f"{SYSTEM_INSTRUCTION}\n{character.persona}\n{languages.instruction(language)}\n{about}".strip(),
        "speech_config": {"voice_config": {"prebuilt_voice_config": {"voice_name": character.gemini_voice}}},
        "input_audio_transcription": {},
        "output_audio_transcription": {},
        "realtime_input_config": {"automatic_activity_detection": {"disabled": True}},
        "tools": declarations(),
        "context_window_compression": {"sliding_window": {}},
    }


def events_from(message) -> list[LiveEvent]:
    """A single message can carry several parts, so every part is read."""
    found: list[LiveEvent] = []
    content = message.server_content
    if content is not None:
        if content.input_transcription and content.input_transcription.text:
            found.append(HeardText(content.input_transcription.text))
        if content.interrupted:
            found.append(ReplyInterrupted())
        if content.model_turn and content.model_turn.parts:
            for part in content.model_turn.parts:
                if part.inline_data and part.inline_data.data:
                    found.append(ReplyAudio(part.inline_data.data))
        if content.output_transcription and content.output_transcription.text:
            found.append(ReplyText(content.output_transcription.text))
        if content.turn_complete:
            found.append(ReplyDone())
    if message.tool_call and message.tool_call.function_calls:
        for call in message.tool_call.function_calls:
            found.append(ToolRequest(call.id or "", call.name or "", dict(call.args or {})))
    if message.go_away is not None:
        found.append(EndingSoon())
    return found


class GeminiSession:
    def __init__(self, types_module, context, session):
        self._types = types_module
        self._context = context
        self._session = session

    async def send_text(self, text: str) -> None:
        turn = self._types.Content(role="user", parts=[self._types.Part(text=text)])
        await self._session.send_client_content(turns=turn, turn_complete=True)

    async def send_note(self, text: str) -> None:
        """Adds to the conversation without asking for a reply yet."""
        turn = self._types.Content(role="user", parts=[self._types.Part(text=text)])
        await self._session.send_client_content(turns=turn, turn_complete=False)

    async def send_image(self, jpeg: bytes) -> None:
        """Goes in the same stream as typed text. Sent as video, Gemini did not see it before answering."""
        picture = self._types.Blob(data=jpeg, mime_type="image/jpeg")
        turn = self._types.Content(role="user", parts=[self._types.Part(inline_data=picture)])
        await self._session.send_client_content(turns=turn, turn_complete=False)

    async def start_speech(self) -> None:
        await self._session.send_realtime_input(activity_start=self._types.ActivityStart())

    async def send_speech(self, pcm16k: bytes) -> None:
        await self._session.send_realtime_input(audio=self._types.Blob(data=pcm16k, mime_type=INPUT_MIME))

    async def end_speech(self) -> None:
        await self._session.send_realtime_input(activity_end=self._types.ActivityEnd())

    async def send_tool_result(self, call_id: str, name: str, result: dict, later: bool = False) -> None:
        scheduling = self._types.FunctionResponseScheduling.WHEN_IDLE if later else None
        response = self._types.FunctionResponse(id=call_id, name=name, response=result, scheduling=scheduling)
        await self._session.send_tool_response(function_responses=[response])

    async def events(self) -> AsyncIterator[LiveEvent]:
        # receive() ends after each completed turn, so it is called again in a loop.
        while True:
            async for message in self._session.receive():
                for event in events_from(message):
                    yield event

    async def close(self) -> None:
        await self._context.__aexit__(None, None, None)


async def connect(
    api_key: str, model: str, character_id: str = DEFAULT_CHARACTER, language: str = "en", about: str = ""
) -> GeminiSession:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    context = client.aio.live.connect(model=model, config=session_config(character_id, language, about))
    session = await context.__aenter__()
    return GeminiSession(types, context, session)
