"""Plays ALPHA's replies, and says when the last sound has really been played."""

from __future__ import annotations

import logging
import math
import struct
import threading
from typing import Callable

from .hub import AudioHub
from .signal import Resampler, level_of

log = logging.getLogger(__name__)

REPLY_RATE = 24_000
_START_BUFFER_SECONDS = 0.12
_SILENCE_BEFORE_DONE_SECONDS = 0.25


class Speaker:
    """The output stream is open only while there is something to play."""

    def __init__(
        self,
        hub: AudioHub,
        device_name: Callable[[], str],
        on_started: Callable[[], None],
        on_finished: Callable[[], None],
        on_error: Callable[[str], None],
    ):
        self._hub = hub
        self._device_name = device_name
        self._on_started = on_started
        self._on_finished = on_finished
        self._on_error = on_error
        self._lock = threading.Lock()
        self._buffer = bytearray()
        self._early: list[bytes] = []
        self._stream = None
        self._opening = False
        self._resampler: Resampler | None = None
        self._device_rate = 0
        self._reply_complete = False
        self._started = False
        self._silent_bytes = 0
        self._close_queued = False
        self._test_device: str | None = None
        self.level = 0.0

    def play(self, pcm24k: bytes) -> None:
        with self._lock:
            self._reply_complete = False
            if self._resampler is not None:
                self._buffer.extend(self._resampler.process(pcm24k))
                return
            self._early.append(pcm24k)
            if self._opening:
                return
            self._opening = True
        self._hub.submit(self._open)

    def reply_complete(self) -> None:
        """No more sound is coming for this reply. If none came at all, finish now."""
        with self._lock:
            self._reply_complete = True
            idle = self._stream is None and not self._opening
        if idle:
            self._on_finished()

    def stop_now(self) -> None:
        with self._lock:
            self._buffer.clear()
            self._early.clear()
            self._reply_complete = True

    def _open(self, backend) -> None:
        stream = None
        try:
            device = self._device_name() if self._test_device is None else self._test_device
            self._test_device = None
            stream = backend.open_output(device, self._fill)
            with self._lock:
                self._device_rate = stream.rate
                self._resampler = Resampler(REPLY_RATE, stream.rate)
                for chunk in self._early:
                    self._buffer.extend(self._resampler.process(chunk))
                self._early.clear()
                self._started = False
                self._silent_bytes = 0
                self._stream = stream
                self._opening = False
            stream.start()
            self._hub.stream_opened()
        except Exception as error:
            log.exception("Speakers did not open")
            with self._lock:
                self._stream = None
                self._opening = False
                self._early.clear()
                self._buffer.clear()
                self._resampler = None
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    log.exception("Speakers did not close after failing to start")
            self._on_error(f"The speakers could not be opened: {error}")
            self._on_finished()
            return
        self._on_started()

    def _fill(self, wanted_bytes: int) -> bytes:
        """Runs on the sound driver's thread, so it never waits on anything slow."""
        with self._lock:
            ready = min(wanted_bytes, len(self._buffer))
            gathering = len(self._buffer) < int(self._device_rate * 2 * _START_BUFFER_SECONDS)
            if not self._started and gathering and not self._reply_complete:
                ready = 0
            data = bytes(self._buffer[:ready])
            del self._buffer[:ready]
            if ready:
                self._started = True
                self._silent_bytes = 0
            else:
                self._silent_bytes += wanted_bytes
            # The tail of silence lets the driver play out what it already holds.
            drained = self._silent_bytes > self._device_rate * 2 * _SILENCE_BEFORE_DONE_SECONDS
            done = self._reply_complete and not self._buffer and (drained or not self._started)
            if done and not self._close_queued:
                self._close_queued = True
                self._hub.submit(self._close)
        self.level = level_of(data)
        return data + b"\0" * (wanted_bytes - len(data))

    def _close(self, _backend) -> None:
        with self._lock:
            self._close_queued = False
            if self._buffer or not self._reply_complete:
                return  # A new reply arrived while this close was waiting.
            stream, self._stream = self._stream, None
            self._resampler = None
        self.level = 0.0
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                log.exception("Speakers did not close cleanly")
            self._hub.stream_closed()
        with self._lock:
            reopening = self._opening
        if not reopening:
            self._on_finished()

    def play_test_tone(self, device_name: str) -> None:
        """A short, quiet rising tone, for checking a choice in Settings before saving it."""
        self._test_device = device_name
        count = REPLY_RATE * 6 // 10
        samples = []
        for index in range(count):
            fade = min(1.0, index / 1200, (count - index) / 1200)
            pitch = 420 + 260 * index / count
            samples.append(int(5000 * fade * math.sin(2 * math.pi * pitch * index / REPLY_RATE)))
        self.play(struct.pack(f"<{count}h", *samples))
        self.reply_complete()
