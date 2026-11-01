"""The real sound hardware, reached through sounddevice.

Only `AudioHub` calls this class, always from its one worker thread, because
the library underneath is not safe to use from several threads.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol


@dataclass(frozen=True)
class AudioDevice:
    name: str
    is_default: bool = False


class AudioStream(Protocol):
    rate: int

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def close(self) -> None: ...


class AudioBackend(Protocol):
    def list_inputs(self) -> list[AudioDevice]: ...
    def list_outputs(self) -> list[AudioDevice]: ...
    def open_input(self, device_name: str, on_audio: Callable[[bytes], None]) -> AudioStream: ...
    def open_output(self, device_name: str, on_need: Callable[[int], bytes]) -> AudioStream: ...


class _Stream:
    def __init__(self, stream, rate: int):
        self._stream = stream
        self.rate = rate

    def start(self) -> None:
        self._stream.start()

    def stop(self) -> None:
        self._stream.stop()

    def close(self) -> None:
        self._stream.close()


class SoundDeviceBackend:
    """Prefers WASAPI, which gives whole device names and a single list per device."""

    def __init__(self):
        import sounddevice

        self._sd = sounddevice

    def refresh(self) -> None:
        """Asks the system for its current devices. Only call with no stream open."""
        self._sd._terminate()
        self._sd._initialize()

    def _host_api(self) -> dict:
        apis = self._sd.query_hostapis()
        for api in apis:
            if api["name"] == "Windows WASAPI":
                return api
        return apis[0]

    def _list(self, channel_key: str, default_key: str) -> list[AudioDevice]:
        api = self._host_api()
        default_index = api[default_key]
        found = []
        for index in api["devices"]:
            info = self._sd.query_devices(index)
            if info[channel_key] > 0:
                found.append(AudioDevice(info["name"], index == default_index))
        return found

    def list_inputs(self) -> list[AudioDevice]:
        return self._list("max_input_channels", "default_input_device")

    def list_outputs(self) -> list[AudioDevice]:
        return self._list("max_output_channels", "default_output_device")

    def _resolve(self, device_name: str, channel_key: str, default_key: str) -> int:
        api = self._host_api()
        for index in api["devices"]:
            info = self._sd.query_devices(index)
            if info["name"] == device_name and info[channel_key] > 0:
                return index
        return api[default_key]

    def open_input(self, device_name: str, on_audio: Callable[[bytes], None]) -> _Stream:
        index = self._resolve(device_name, "max_input_channels", "default_input_device")
        rate = int(self._sd.query_devices(index)["default_samplerate"])

        def callback(data, _frames, _time, _status):
            on_audio(bytes(data))

        stream = self._sd.RawInputStream(
            device=index, samplerate=rate, channels=1, dtype="int16", blocksize=rate // 10, callback=callback
        )
        return _Stream(stream, rate)

    def open_output(self, device_name: str, on_need: Callable[[int], bytes]) -> _Stream:
        index = self._resolve(device_name, "max_output_channels", "default_output_device")
        rate = int(self._sd.query_devices(index)["default_samplerate"])

        def callback(out, frames, _time, _status):
            out[:] = on_need(frames * 2)

        stream = self._sd.RawOutputStream(
            device=index, samplerate=rate, channels=1, dtype="int16", latency=0.08, callback=callback
        )
        return _Stream(stream, rate)
