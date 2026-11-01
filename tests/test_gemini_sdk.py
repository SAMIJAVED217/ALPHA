"""The real Google SDK, connected to a stand-in service on this machine."""

import asyncio
import base64
import types as builtin_types

import pytest

from alpha.settings import DEFAULT_MODEL
from alpha.voice import gemini
from alpha.voice.events import (
    EndingSoon,
    HeardText,
    ReplyAudio,
    ReplyDone,
    ReplyInterrupted,
    ReplyText,
    ToolRequest,
)
from alpha.voice.persona import PROPOSE_TOOL_NAME

from .fake_live_server import GOOD_KEY, REPLY_TEXT, FakeLiveServer, point_sdk_at


@pytest.fixture
def server():
    server = FakeLiveServer()
    server.start()
    point_sdk_at(server.port)
    yield server
    server.stop()


async def collect_turn(session):
    found = []
    async for event in session.events():
        found.append(event)
        if isinstance(event, (ReplyDone, ToolRequest)):
            return found
    return found


def run(coroutine):
    return asyncio.run(asyncio.wait_for(coroutine, 10))


def test_setup_asks_for_audio_with_manual_turns_and_the_one_tool(server):
    async def scenario():
        session = await gemini.connect(GOOD_KEY, DEFAULT_MODEL)
        await session.close()

    run(scenario())
    setup = server.frames[0]["setup"]
    assert setup["model"] == f"models/{DEFAULT_MODEL}"
    assert setup["generationConfig"]["responseModalities"] == ["AUDIO"]
    assert setup["realtimeInputConfig"]["automaticActivityDetection"]["disabled"] is True
    assert "inputAudioTranscription" in setup and "outputAudioTranscription" in setup
    assert "APLHA" in setup["systemInstruction"]["parts"][0]["text"]
    names = [f["name"] for tool in setup["tools"] for f in tool["functionDeclarations"]]
    assert PROPOSE_TOOL_NAME in names and "set_volume" in names
    assert server.keys == [GOOD_KEY]


def test_text_turn_round_trip(server):
    async def scenario():
        session = await gemini.connect(GOOD_KEY, DEFAULT_MODEL)
        await session.send_text("hello")
        found = await collect_turn(session)
        await session.send_text("and again")
        found += await collect_turn(session)
        await session.close()
        return found

    found = run(scenario())
    assert [f for f in found if isinstance(f, ReplyText)] == [ReplyText(REPLY_TEXT)] * 2
    assert sum(isinstance(f, ReplyAudio) for f in found) == 2
    audio = next(f for f in found if isinstance(f, ReplyAudio))
    assert len(audio.pcm24k) == 2 * int(24_000 * 0.7)
    turn = server.frames[1]["clientContent"]
    assert turn["turnComplete"] is True
    assert turn["turns"][0]["parts"][0]["text"] == "hello"


def test_voice_turn_sends_activity_markers_and_16k_audio(server):
    pcm = b"\x01\x02" * 1600

    async def scenario():
        session = await gemini.connect(GOOD_KEY, DEFAULT_MODEL)
        await session.start_speech()
        await session.send_speech(pcm)
        await session.end_speech()
        found = await collect_turn(session)
        await session.close()
        return found

    found = run(scenario())
    assert HeardText("what the local mic heard") in found
    sent = [frame["realtimeInput"] for frame in server.frames[1:]]
    assert "activityStart" in sent[0]
    assert sent[1]["audio"]["mimeType"] == "audio/pcm;rate=16000"
    assert base64.b64decode(sent[1]["audio"]["data"]) == pcm
    assert "activityEnd" in sent[2]


def test_tool_call_and_its_result(server):
    async def scenario():
        session = await gemini.connect(GOOD_KEY, DEFAULT_MODEL)
        await session.send_text("please make a file")
        request = (await collect_turn(session))[-1]
        await session.send_tool_result(request.call_id, request.name, {"created": True}, later=True)
        found = await collect_turn(session)
        await session.close()
        return request, found

    request, found = run(scenario())
    assert request == ToolRequest(
        "call-1", PROPOSE_TOOL_NAME, {"file_name": "from-model.txt", "content": "Written by the model."}
    )
    assert ReplyDone() in found
    response = next(f for f in server.frames if "toolResponse" in f)["toolResponse"]["functionResponses"][0]
    assert response["id"] == "call-1"
    assert response["response"] == {"created": True}
    assert response["scheduling"] == "WHEN_IDLE"


def test_rejected_key_surfaces_as_a_failure(server):
    async def scenario():
        session = await gemini.connect("wrong-key", DEFAULT_MODEL)
        await session.send_text("hello")
        await collect_turn(session)

    with pytest.raises(Exception) as caught:
        run(scenario())
    assert "API key" in str(caught.value)


def message(**fields):
    defaults = {"server_content": None, "tool_call": None, "go_away": None}
    return builtin_types.SimpleNamespace(**{**defaults, **fields})


def test_every_part_of_one_message_is_read():
    part = lambda data: builtin_types.SimpleNamespace(inline_data=builtin_types.SimpleNamespace(data=data))
    content = builtin_types.SimpleNamespace(
        input_transcription=None,
        interrupted=False,
        model_turn=builtin_types.SimpleNamespace(parts=[part(b"a"), part(b"b"), part(b"c")]),
        output_transcription=None,
        turn_complete=True,
    )
    assert gemini.events_from(message(server_content=content)) == [
        ReplyAudio(b"a"),
        ReplyAudio(b"b"),
        ReplyAudio(b"c"),
        ReplyDone(),
    ]


def test_interruption_and_ending_notices_are_recognised():
    content = builtin_types.SimpleNamespace(
        input_transcription=None, interrupted=True, model_turn=None, output_transcription=None, turn_complete=False
    )
    assert gemini.events_from(message(server_content=content)) == [ReplyInterrupted()]
    assert gemini.events_from(message(go_away=object())) == [EndingSoon()]
