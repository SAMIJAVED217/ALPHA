"""The push-to-talk microphone. Closed unless someone is holding the control."""

from __future__ import annotations

import logging
import threading
from enum import Enum
from typing import Callable

from .hub import AudioHub
from .signal import Resampler, level_of

log = logging.getLogger(__name__)

OUTPUT_RATE = 16_000
CHUNK_BYTES = 3_200  # 100 ms of 16 kHz, 16-bit sound.


class MicState(str, Enum):
    CLOSED = "closed"
    OPENING = "opening"
    OPEN = "open"
    CLOSING = "closing"


class Microphone:
    """`on_state` reports what really happened, and `on_chunk` gets 16 kHz sound.

    The state leaves CLOSED before the device is touched, and only returns to
    CLOSED after it has been released, so an indicator tied to this state can
    never show "closed" while the microphone is live.
    """

    def __init__(
        self,
        hub: AudioHub,
        device_name: Callable[[], str],
        on_chunk: Callable[[bytes], None],
        on_state: Callable[[MicState], None],
        on_error: Callable[[str], None],
    ):
        self._hub = hub
        self._device_name = device_name
        self._on_chunk = on_chunk
        self._on_state = on_state
        self._on_error = on_error
        self._lock = threading.Lock()
        self._want_open = False
        self._stream = None
        self._state = MicState.CLOSED
        self._pending = bytearray()
        self._resampler: Resampler | None = None
        self.level = 0.0

    @property
    def state(self) -> MicState:
        return self._state

    def _set_state(self, state: MicState) -> None:
        self._state = state
        self._on_state(state)

    def press(self) -> None:
        with self._lock:
            if self._want_open:
                return
            self._want_open = True
            if self._state is MicState.CLOSED:
                self._set_state(MicState.OPENING)
        self._hub.submit(self._settle)

    def release(self) -> None:
        with self._lock:
            if not self._want_open:
                return
            self._want_open = False
        self._hub.submit(self._settle)

    def _settle(self, backend) -> None:
        """Runs on the audio thread and moves the device towards what is wanted."""
        with self._lock:
            want_open = self._want_open
        if want_open and self._stream is None:
            self._open(backend)
        elif not want_open and self._stream is not None:
            self._close()
        elif not want_open and self._state is not MicState.CLOSED:
            self._set_state(MicState.CLOSED)

    def _open(self, backend) -> None:
        if self._state is not MicState.OPENING:
            self._set_state(MicState.OPENING)
        self._pending.clear()
        try:
            stream = backend.open_input(self._device_name(), self._receive)
            self._resampler = Resampler(stream.rate, OUTPUT_RATE)
            self._hub.stream_opened()
            self._stream = stream
            stream.start()
        except Exception as error:
            log.exception("Microphone did not open")
            self._close()
            with self._lock:
                self._want_open = False
            self._set_state(MicState.CLOSED)
            self._on_error(f"The microphone could not be opened: {error}")
            return
        self._set_state(MicState.OPEN)

    def _close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is None:
            return
        self._set_state(MicState.CLOSING)
        try:
            stream.stop()
        except Exception:
            log.exception("Microphone did not stop cleanly")
        try:
            stream.close()
        except Exception:
            log.exception("Microphone did not close cleanly")
        self._hub.stream_closed()
        self.level = 0.0
        if self._pending:
            self._on_chunk(bytes(self._pending))
            self._pending.clear()
        self._set_state(MicState.CLOSED)

    def _receive(self, raw: bytes) -> None:
        """Runs on the sound driver's thread, so it only does arithmetic."""
        converted = self._resampler.process(raw)
        self.level = level_of(raw)
        self._pending.extend(converted)
        while len(self._pending) >= CHUNK_BYTES:
            chunk = bytes(self._pending[:CHUNK_BYTES])
            del self._pending[:CHUNK_BYTES]
            self._on_chunk(chunk)
