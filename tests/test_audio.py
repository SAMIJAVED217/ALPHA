import threading
import time

import numpy as np
import pytest

from alpha.audio.hub import AudioHub
from alpha.audio.microphone import CHUNK_BYTES, MicState, Microphone
from alpha.audio.signal import Resampler, level_of
from alpha.audio.speaker import Speaker

from .fakes import FakeBackend


def wait_until(condition, seconds=3.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.005)
    return condition()


def tone(rate, seconds=1.0, pitch=440.0, loudness=12_000):
    times = np.arange(int(rate * seconds)) / rate
    return (np.sin(2 * np.pi * pitch * times) * loudness).astype("<i2").tobytes()


@pytest.fixture
def backend():
    return FakeBackend()


@pytest.fixture
def hub(backend):
    hub = AudioHub(lambda: backend)
    yield hub
    hub.shutdown()


def test_level_of_silence_and_speech():
    assert level_of(b"") == 0.0
    assert level_of(b"\0" * 1000) == 0.0
    assert 0.3 < level_of(tone(16_000)) <= 1.0


@pytest.mark.parametrize("source,target", [(48_000, 16_000), (44_100, 16_000), (24_000, 48_000), (24_000, 44_100)])
def test_resampler_keeps_length_and_pitch_across_chunks(source, target):
    data = tone(source)
    resampler = Resampler(source, target)
    pieces = [resampler.process(data[i : i + 960]) for i in range(0, len(data), 960)]
    out = np.frombuffer(b"".join(pieces), dtype="<i2").astype(float)
    assert abs(out.size - target) < target * 0.01
    spectrum = np.abs(np.fft.rfft(out))
    peak = np.argmax(spectrum) * target / out.size
    assert abs(peak - 440) < 5


def test_resampler_is_a_no_op_at_the_same_rate():
    assert Resampler(16_000, 16_000).process(b"\x01\x02") == b"\x01\x02"


def test_all_device_work_runs_on_one_thread(backend, hub):
    futures = [hub.submit(lambda b: b.list_outputs()) for _ in range(5)]
    for future in futures:
        future.result(2)
    hub.submit(lambda b: b.open_input("", lambda data: None)).result(2)
    assert len(backend.threads) == 1
    assert next(iter(backend.threads)).startswith("alpha-audio")


def test_enumeration_never_runs_twice_at_once():
    backend = FakeBackend(list_delay=0.05)
    hub = AudioHub(lambda: backend)
    results = []
    threads = [threading.Thread(target=lambda: results.append(hub.list_devices().result(3))) for _ in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    hub.shutdown()
    assert backend.most_listing_at_once == 1
    assert backend.list_calls < 12
    assert all(result.inputs[0].name == "Test mic" for result in results)


def test_backend_is_built_on_the_audio_thread_not_the_caller():
    names = []

    def factory():
        names.append(threading.current_thread().name)
        return FakeBackend()

    hub = AudioHub(factory)
    assert names == []
    hub.list_devices().result(2)
    hub.shutdown()
    assert names[0].startswith("alpha-audio")


class MicRig:
    def __init__(self, hub):
        self.chunks = []
        self.states = []
        self.errors = []
        self.mic = Microphone(hub, lambda: "Desk mic", self.chunks.append, self.states.append, self.errors.append)


def test_mic_is_closed_until_pressed_and_closed_after_release(backend, hub):
    rig = MicRig(hub)
    assert backend.opened_inputs == []
    rig.mic.press()
    assert wait_until(lambda: rig.mic.state is MicState.OPEN)
    assert backend.mic_is_open
    assert backend.opened_inputs[0][0] == "Desk mic"
    rig.mic.release()
    assert wait_until(lambda: rig.mic.state is MicState.CLOSED)
    assert not backend.mic_is_open
    assert backend.opened_inputs[0][1].closed
    assert rig.states == [MicState.OPENING, MicState.OPEN, MicState.CLOSING, MicState.CLOSED]


def test_indicator_state_is_never_closed_while_the_device_is_live(backend, hub):
    seen = []
    rig = MicRig(hub)
    rig.mic._on_state = lambda state: seen.append((state, backend.mic_is_open))
    rig.mic.press()
    assert wait_until(lambda: rig.mic.state is MicState.OPEN)
    rig.mic.release()
    assert wait_until(lambda: rig.mic.state is MicState.CLOSED)
    assert (MicState.CLOSED, True) not in seen
    assert seen[-1] == (MicState.CLOSED, False)


def test_quick_tap_leaves_the_mic_closed(backend, hub):
    rig = MicRig(hub)
    for _ in range(25):
        rig.mic.press()
        rig.mic.release()
    hub.submit(lambda b: None).result(3)
    assert wait_until(lambda: rig.mic.state is MicState.CLOSED)
    assert not backend.mic_is_open
    assert all(stream.closed for _, stream in backend.opened_inputs)


def test_mic_turns_device_audio_into_100ms_chunks_at_16k(backend, hub):
    rig = MicRig(hub)
    rig.mic.press()
    assert wait_until(lambda: rig.mic.state is MicState.OPEN)
    data = tone(48_000)
    for start in range(0, len(data), 1920):
        backend.feed(data[start : start + 1920])
    assert rig.mic.level > 0.3
    rig.mic.release()
    assert wait_until(lambda: rig.mic.state is MicState.CLOSED)
    assert rig.mic.level == 0.0
    total = sum(len(chunk) for chunk in rig.chunks)
    assert abs(total - 32_000) < 400
    assert all(len(chunk) == CHUNK_BYTES for chunk in rig.chunks[:-1])


def test_mic_failure_is_reported_and_leaves_it_closed(hub, backend):
    backend.fail_input = True
    rig = MicRig(hub)
    rig.mic.press()
    assert wait_until(lambda: rig.errors)
    assert "could not be opened" in rig.errors[0]
    assert wait_until(lambda: rig.mic.state is MicState.CLOSED)


class SpeakerRig:
    def __init__(self, hub):
        self.started = []
        self.finished = []
        self.errors = []
        self.speaker = Speaker(
            hub,
            lambda: "Headphones",
            lambda: self.started.append(True),
            lambda: self.finished.append(True),
            self.errors.append,
        )


def drain(backend, bytes_each=1920, rounds=400):
    played = bytearray()
    for _ in range(rounds):
        if backend.pull is None:
            break
        if backend.opened_outputs and backend.opened_outputs[-1][1].closed:
            break
        played.extend(backend.pull(bytes_each))
        time.sleep(0.001)
    return bytes(played)


def test_speaker_plays_everything_then_reports_finished(backend, hub):
    rig = SpeakerRig(hub)
    data = tone(24_000, 0.5)
    rig.speaker.play(data)
    assert wait_until(lambda: rig.started)
    rig.speaker.reply_complete()
    assert rig.finished == []
    played = drain(backend)
    assert wait_until(lambda: rig.finished)
    assert len(rig.finished) == 1
    audible = np.count_nonzero(np.frombuffer(played, dtype="<i2")) / 48_000
    assert 0.45 < audible < 0.55
    assert backend.opened_outputs[-1][1].closed
    assert backend.opened_outputs[-1][0] == "Headphones"


def test_speaker_stays_shut_when_there_is_nothing_to_play(backend, hub):
    rig = SpeakerRig(hub)
    rig.speaker.reply_complete()
    assert rig.finished == [True]
    assert backend.opened_outputs == []


def test_speaker_level_follows_the_sound_being_played(backend, hub):
    rig = SpeakerRig(hub)
    rig.speaker.play(tone(24_000, 0.5))
    assert wait_until(lambda: rig.started)
    backend.pull(1920)
    assert rig.speaker.level > 0.2


def test_stop_now_discards_what_is_waiting(backend, hub):
    rig = SpeakerRig(hub)
    rig.speaker.play(tone(24_000, 2.0))
    assert wait_until(lambda: rig.started)
    rig.speaker.stop_now()
    drain(backend)
    assert wait_until(lambda: rig.finished)


def test_speaker_failure_is_reported_and_still_finishes(backend, hub):
    backend.fail_output = True
    rig = SpeakerRig(hub)
    rig.speaker.play(tone(24_000, 0.1))
    assert wait_until(lambda: rig.errors and rig.finished)
    assert rig.started == []


def test_test_tone_uses_the_chosen_device_not_the_saved_one(backend, hub):
    rig = SpeakerRig(hub)
    rig.speaker.play_test_tone("Chosen speakers")
    assert wait_until(lambda: rig.started)
    assert backend.opened_outputs[0][0] == "Chosen speakers"
    drain(backend)
    assert wait_until(lambda: rig.finished)
    rig.speaker.play(tone(24_000, 0.2))
    assert wait_until(lambda: len(backend.opened_outputs) == 2)
    assert backend.opened_outputs[1][0] == "Headphones"
