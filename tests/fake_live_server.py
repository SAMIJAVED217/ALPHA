"""A stand-in for the Gemini Live service that runs on this machine only.

It speaks the same websocket messages as the real service, so the real Google
SDK can connect to it. It proves ALPHA's use of the SDK, not Gemini itself.
"""

from __future__ import annotations

import asyncio
import base64
import json
import math
import struct
import threading

from websockets.asyncio.server import serve

GOOD_KEY = "local-test-key"
REPLY_TEXT = "Hello from the local stand-in."


def reply_tone(seconds: float = 0.7) -> bytes:
    count = int(24_000 * seconds)
    samples = (int(9000 * math.sin(2 * math.pi * 330 * i / 24_000)) for i in range(count))
    return struct.pack(f"<{count}h", *samples)


def _camel(value):
    """The SDK sends a mix of snake_case and camelCase, and the service accepts both."""
    if isinstance(value, dict):
        converted = {}
        for key, item in value.items():
            head, *rest = key.split("_")
            converted[head + "".join(word.title() for word in rest)] = _camel(item)
        return converted
    if isinstance(value, list):
        return [_camel(item) for item in value]
    return value


def point_sdk_at(port: int) -> None:
    """Makes the real SDK connect here, unencrypted. The rest of the SDK runs as normal."""
    from google.genai._api_client import BaseApiClient

    BaseApiClient._websocket_base_url = lambda self, http_options=None: f"ws://127.0.0.1:{port}"
    BaseApiClient._ensure_websocket_ssl_ctx = staticmethod(lambda *args, **kwargs: {})


class FakeLiveServer:
    def __init__(self, reply_delay: float = 0.0, reply_seconds: float = 0.7):
        self.reply_delay = reply_delay
        self.reply_seconds = reply_seconds
        self.frames: list[dict] = []
        self.keys: list[str] = []
        self.port = 0
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._stop: asyncio.Future | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        self._thread.start()
        self._ready.wait(5)

    def stop(self) -> None:
        self._loop.call_soon_threadsafe(self._stop.set_result, None)
        self._thread.join(5)

    def _run(self) -> None:
        self._loop.run_until_complete(self._serve())

    async def _serve(self) -> None:
        self._stop = self._loop.create_future()
        async with serve(self._handle, "127.0.0.1", 0) as server:
            self.port = server.sockets[0].getsockname()[1]
            self._ready.set()
            await self._stop

    async def _reply(self, socket) -> None:
        await asyncio.sleep(self.reply_delay)
        chunk = base64.b64encode(reply_tone(self.reply_seconds)).decode()
        await socket.send(
            json.dumps(
                {
                    "serverContent": {
                        "modelTurn": {"parts": [{"inlineData": {"mimeType": "audio/pcm;rate=24000", "data": chunk}}]},
                        "outputTranscription": {"text": REPLY_TEXT},
                    }
                }
            )
        )
        await socket.send(json.dumps({"serverContent": {"turnComplete": True}}))

    async def _handle(self, socket) -> None:
        key = socket.request.headers.get("x-goog-api-key", "")
        self.keys.append(key)
        self.frames.append(_camel(json.loads(await socket.recv())))
        if key != GOOD_KEY:
            await socket.close(1008, "API key not valid. Please pass a valid API key.")
            return
        await socket.send(json.dumps({"setupComplete": {}}))
        async for raw in socket:
            frame = _camel(json.loads(raw))
            self.frames.append(frame)
            if "clientContent" in frame:
                text = frame["clientContent"]["turns"][0]["parts"][0]["text"]
                if "make a file" in text:
                    call = {"id": "call-1", "name": "propose_file_creation",
                            "args": {"file_name": "from-model.txt", "content": "Written by the model."}}
                    await socket.send(json.dumps({"toolCall": {"functionCalls": [call]}}))
                else:
                    await self._reply(socket)
            elif "toolResponse" in frame:
                await self._reply(socket)
            elif "activityEnd" in frame.get("realtimeInput", {}):
                await socket.send(
                    json.dumps({"serverContent": {"inputTranscription": {"text": "what the local mic heard"}}})
                )
                await self._reply(socket)
