"""One worker thread that owns all sound-device access.

Every open, close and enumeration runs here, one at a time, so callers never
touch the library directly and never wait for it on the interface thread.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, TypeVar

from .backend import AudioBackend, AudioDevice

log = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass(frozen=True)
class DeviceList:
    inputs: list[AudioDevice]
    outputs: list[AudioDevice]


def _default_backend() -> AudioBackend:
    from .backend import SoundDeviceBackend

    return SoundDeviceBackend()


class AudioHub:
    def __init__(self, backend_factory: Callable[[], AudioBackend] = _default_backend):
        self._factory = backend_factory
        self._backend: AudioBackend | None = None
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="alpha-audio")
        self._lock = threading.Lock()
        self._listing: Future | None = None
        self._open_streams = 0

    def submit(self, job: Callable[[AudioBackend], T]) -> Future:
        """Runs `job(backend)` on the audio thread."""
        return self._pool.submit(self._run, job)

    def _run(self, job: Callable[[AudioBackend], T]) -> T:
        if self._backend is None:
            self._backend = self._factory()
        return job(self._backend)

    def stream_opened(self) -> None:
        self._open_streams += 1

    def stream_closed(self) -> None:
        self._open_streams -= 1

    def list_devices(self) -> Future:
        """Starts an enumeration, or returns the one already running."""
        with self._lock:
            if self._listing is None or self._listing.done():
                self._listing = self.submit(self._enumerate)
            return self._listing

    def _enumerate(self, backend: AudioBackend) -> DeviceList:
        refresh = getattr(backend, "refresh", None)
        if refresh is not None and self._open_streams == 0:
            refresh()
        return DeviceList(backend.list_inputs(), backend.list_outputs())

    def shutdown(self) -> None:
        self._pool.shutdown(wait=True, cancel_futures=False)
