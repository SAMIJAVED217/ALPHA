"""Stand-ins for the sound hardware and for a live session."""

from __future__ import annotations

import asyncio
import threading
import time

from alpha.audio.backend import AudioDevice


class FakeStream:
    def __init__(self, rate, on_close=None):
        self.rate = rate
        self.started = False
        self.closed = False
        self._on_close = on_close

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def close(self):
        self.closed = True
        if self._on_close:
            self._on_close()


class FakeBackend:
    def __init__(self, rate=48_000, list_delay=0.0, fail_input=False, fail_output=False):
        self.rate = rate
        self.list_delay = list_delay
        self.fail_input = fail_input
        self.fail_output = fail_output
        self.inputs = [AudioDevice("Test mic", True)]
        self.outputs = [AudioDevice("Test speakers", True)]
        self.opened_inputs = []
        self.opened_outputs = []
        self.threads = set()
        self.listing_now = 0
        self.most_listing_at_once = 0
        self.list_calls = 0
        self.feed = None
        self.pull = None
        self._guard = threading.Lock()

    def _note_thread(self):
        self.threads.add(threading.current_thread().name)

    def list_inputs(self):
        self._note_thread()
        with self._guard:
            self.listing_now += 1
            self.list_calls += 1
            self.most_listing_at_once = max(self.most_listing_at_once, self.listing_now)
        time.sleep(self.list_delay)
        with self._guard:
            self.listing_now -= 1
        return self.inputs

    def list_outputs(self):
        self._note_thread()
        return self.outputs

    def open_input(self, device_name, on_audio):
        self._note_thread()
        if self.fail_input:
            raise OSError("no such device")
        self.feed = on_audio
        stream = FakeStream(self.rate)
        self.opened_inputs.append((device_name, stream))
        return stream

    def open_output(self, device_name, on_need):
        self._note_thread()
        if self.fail_output:
            raise OSError("no such device")
        self.pull = on_need
        stream = FakeStream(self.rate)
        self.opened_outputs.append((device_name, stream))
        return stream

    @property
    def mic_is_open(self):
        return any(stream.started for _, stream in self.opened_inputs)


class ScriptedSession:
    """Records what ALPHA sends, and gives back whatever a test feeds it."""

    def __init__(self):
        self.sent = []
        self.closed = False
        self._events = None

    def _queue(self):
        if self._events is None:
            self._events = asyncio.Queue()
        return self._events

    async def send_text(self, text):
        self.sent.append(("text", text))

    async def send_note(self, text):
        self.sent.append(("note", text))

    async def send_image(self, jpeg):
        self.sent.append(("image", len(jpeg)))

    async def start_speech(self):
        self.sent.append(("start", None))

    async def send_speech(self, pcm):
        self.sent.append(("speech", len(pcm)))

    async def end_speech(self):
        self.sent.append(("end", None))

    async def send_tool_result(self, call_id, name, result, later=False):
        self.sent.append(("tool", call_id, name, result))

    async def events(self):
        while True:
            item = await self._queue().get()
            if isinstance(item, Exception):
                raise item
            yield item

    async def close(self):
        self.closed = True

    def give(self, loop, event):
        loop.call_soon_threadsafe(self._queue().put_nowait, event)
