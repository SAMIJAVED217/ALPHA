import pytest

from alpha import paths
from alpha.audio.hub import AudioHub
from alpha.settings import Settings
from alpha.state import State
from alpha.voice import conversation as conversation_module
from alpha.voice import wake_model
from alpha.voice.conversation import Conversation
from alpha.voice.events import ReplyAudio, ReplyDone
from alpha.voice.hands_free import CHUNK_SECONDS, Mode, SpeechTracker

from .fakes import FakeBackend, ScriptedSession
from .test_audio import drain, tone
from .test_interface import spin_until as wait_until

SPEECH = tone(48_000, 0.1)
QUIET = b"\0" * len(SPEECH)


class FakeWake:
    def __init__(self, ready, wake, gone):
        self.ready, self.wake, self.gone = ready, wake, gone
        self.fed = []
        self.stopped = False

    def feed(self, chunk):
        self.fed.append(chunk)

    def stop(self):
        self.stopped = True


class Rig:
    def __init__(self, key="a-key", model_ready=True):
        self.backend = FakeBackend()
        self.hub = AudioHub(lambda: self.backend)
        self.sessions = []
        self.wakes = []
        self.modes = []
        self.states = []
        self.notices = []
        self.conversation = Conversation(
            Settings(),
            self.hub,
            connector=self._connect,
            load_key=lambda: key,
            wake_factory=self._wake,
            wake_model_ready=lambda: model_ready,
        )
        c = self.conversation
        c.hands_free_changed.connect(self.modes.append)
        c.state_changed.connect(lambda state, detail: self.states.append(state))
        c.notice.connect(self.notices.append)
        c.start()
        assert wait_until(lambda: self.states)

    async def _connect(self, key, model):
        self.sessions.append(ScriptedSession())
        return self.sessions[-1]

    def _wake(self, ready, wake, gone):
        self.wakes.append(FakeWake(ready, wake, gone))
        return self.wakes[-1]

    @property
    def mode(self):
        return self.conversation._mode

    @property
    def wake(self):
        return self.wakes[-1]

    def turn_on(self):
        self.conversation.set_hands_free(True)
        assert wait_until(lambda: self.backend.mic_is_open and self.mode is Mode.WAITING)

    def sound(self, chunk=SPEECH, times=1):
        for _ in range(times):
            self.backend.feed(chunk)
        self.hub.submit(lambda b: None).result(2)

    def say_wake_word(self):
        self.sound(times=3)
        self.wake.wake()
        assert wait_until(lambda: self.mode is Mode.TURN)

    def speech_sent(self):
        return [item for session in self.sessions for item in session.sent if item[0] == "speech"]

    def finish_turn(self):
        self.sound(times=2)
        self.sound(QUIET, times=12)
        assert wait_until(lambda: self.mode is Mode.REPLYING)
        assert wait_until(lambda: self.sessions and self.sessions[-1].sent[-1] == ("end", None))

    def give(self, event):
        self.sessions[-1].give(self.conversation._loop, event)

    def close(self):
        self.conversation.shutdown()
        self.hub.shutdown()


@pytest.fixture
def rig(qapp):
    made = []

    def make(**options):
        made.append(Rig(**options))
        return made[-1]

    yield make
    for item in made:
        item.close()


def waiting_action(r):
    r.turn_on()
    r.conversation.propose_file("a.txt", "x")
    assert wait_until(lambda: r.conversation._gate.current is not None)
    return r.conversation._gate.current


def test_saying_confirm_carries_out_the_waiting_action(rig):
    r = rig()
    proposal = waiting_action(r)
    r.sound(times=4)
    r.wake.on_decision("confirm")
    assert wait_until(lambda: r.conversation._gate.current is None)
    assert proposal.target.exists()


def test_saying_cancel_drops_the_waiting_action(rig):
    r = rig()
    proposal = waiting_action(r)
    r.sound(times=4)
    r.wake.on_decision("cancel")
    assert wait_until(lambda: r.conversation._gate.current is None)
    assert not proposal.target.exists()


def test_a_decision_word_in_quiet_noise_is_ignored(rig):
    r = rig()
    proposal = waiting_action(r)
    r.wake.on_decision("confirm")
    r.hub.submit(lambda b: None).result(2)
    assert r.conversation._gate.current is proposal


def test_aplhas_own_voice_cannot_confirm(rig):
    r = rig()
    proposal = waiting_action(r)
    r.sound(times=4)
    r.conversation._own_voice_gone_at = r.conversation._loop.time() + 60
    r.wake.on_decision("confirm")
    r.hub.submit(lambda b: None).result(2)
    assert r.conversation._gate.current is proposal


def test_a_decision_word_with_nothing_waiting_does_nothing(rig):
    r = rig()
    r.turn_on()
    r.sound(times=4)
    r.wake.on_decision("confirm")
    assert r.conversation._gate.current is None


def test_talking_while_an_action_waits_does_not_start_a_turn(rig):
    r = rig()
    waiting_action(r)
    r.sound(times=12)
    assert r.mode is Mode.WAITING


def test_off_by_default_and_mic_closed(rig):
    r = rig()
    assert r.mode is Mode.OFF
    assert r.backend.opened_inputs == []
    assert r.wakes == []


def test_building_a_conversation_never_opens_the_mic_by_itself(rig):
    r = rig()
    assert Settings().hands_free_at_start
    assert r.backend.opened_inputs == []


def test_background_sound_goes_only_to_the_local_listener(rig):
    r = rig()
    r.turn_on()
    r.sound(QUIET, times=30)
    assert wait_until(lambda: len(r.wake.fed) >= 25)
    assert r.sessions == []
    assert r.speech_sent() == []


def test_only_the_turn_mode_sends_to_gemini():
    assert [mode for mode in Mode if mode.sends_to_gemini] == [Mode.TURN]


def test_wake_word_starts_a_turn_and_silence_ends_it(rig):
    r = rig()
    r.turn_on()
    r.say_wake_word()
    assert wait_until(lambda: r.states[-1] is State.LISTENING)
    r.finish_turn()
    assert r.speech_sent()
    assert r.backend.mic_is_open


def test_a_wake_word_imagined_in_quiet_noise_is_ignored(rig):
    r = rig()
    r.turn_on()
    r.sound(QUIET, times=5)
    r.wake.wake()
    r.sound(QUIET, times=3)
    assert r.mode is Mode.WAITING
    assert r.sessions == []


def test_a_short_noise_does_not_start_a_turn(rig):
    r = rig()
    r.turn_on()
    r.sound(times=3)
    r.sound(QUIET, times=3)
    assert r.mode is Mode.WAITING
    assert r.sessions == []


def test_simply_speaking_starts_a_turn(rig):
    r = rig()
    r.turn_on()
    r.sound(times=8)
    assert wait_until(lambda: r.mode is Mode.TURN)
    r.finish_turn()
    assert r.speech_sent()


def test_a_steady_fan_stops_counting_as_speech():
    tracker = SpeechTracker()
    assert tracker.is_clear_speech(0.1)
    for _ in range(300):
        tracker.hear_room(0.1)
    assert not tracker.is_clear_speech(0.1)


def test_sound_before_the_wake_word_is_limited_to_a_short_lead_in(rig):
    r = rig()
    r.turn_on()
    r.sound(QUIET, times=100)
    assert wait_until(lambda: len(r.conversation._lead_in) >= 3)
    r.say_wake_word()
    r.finish_turn()
    sent_before_wake = len(r.speech_sent()) - 14
    assert sent_before_wake <= conversation_module.LEAD_IN_CHUNKS


def test_reply_then_follow_up_needs_no_wake_word(rig, monkeypatch):
    monkeypatch.setattr(conversation_module, "FOLLOW_UP_SETTLE_SECONDS", 0.0)
    r = rig()
    r.turn_on()
    r.say_wake_word()
    r.finish_turn()
    r.give(ReplyAudio(tone(24_000, 0.3)))
    r.give(ReplyDone())
    assert wait_until(lambda: State.SPEAKING in r.states)
    drain(r.backend)
    assert wait_until(lambda: r.mode is Mode.FOLLOW_UP)
    r.sound()
    r.sound(QUIET)
    assert r.mode is Mode.FOLLOW_UP
    r.sound(times=4)
    assert wait_until(lambda: r.mode is Mode.TURN)


def test_echo_right_after_a_reply_is_not_taken_for_a_follow_up(rig):
    r = rig()
    r.turn_on()
    r.say_wake_word()
    r.finish_turn()
    r.give(ReplyDone())
    assert wait_until(lambda: r.mode is Mode.FOLLOW_UP)
    r.sound(times=5)
    assert r.mode is Mode.FOLLOW_UP


def test_follow_up_window_closes_back_to_waiting(rig, monkeypatch):
    monkeypatch.setattr(conversation_module, "FOLLOW_UP_SECONDS", 0.05)
    r = rig()
    r.turn_on()
    r.say_wake_word()
    r.finish_turn()
    r.give(ReplyDone())
    assert wait_until(lambda: Mode.FOLLOW_UP in r.modes)
    assert wait_until(lambda: r.mode is Mode.WAITING)


def test_its_own_voice_cannot_wake_it_but_esc_stops_it(rig):
    r = rig()
    r.turn_on()
    r.say_wake_word()
    r.finish_turn()
    r.give(ReplyAudio(tone(24_000, 3.0)))
    assert wait_until(lambda: State.SPEAKING in r.states)
    r.sound(times=2)
    r.wake.wake()
    r.sound(QUIET, times=2)
    assert r.mode is Mode.REPLYING
    r.conversation.interrupt()
    assert wait_until(lambda: len(r.conversation.speaker._buffer) == 0)
    assert wait_until(lambda: r.mode is Mode.FOLLOW_UP)


def test_turning_off_closes_the_mic_and_stops_the_listener(rig):
    r = rig()
    r.turn_on()
    wake = r.wake
    r.conversation.set_hands_free(False)
    assert wait_until(lambda: not r.backend.mic_is_open)
    assert wait_until(lambda: wake.stopped)
    assert r.mode is Mode.OFF


def test_turning_off_mid_turn_ends_the_turn(rig):
    r = rig()
    r.turn_on()
    r.say_wake_word()
    r.sound()
    r.conversation.set_hands_free(False)
    assert wait_until(lambda: not r.backend.mic_is_open)
    assert wait_until(lambda: r.sessions and r.sessions[0].sent[-1] == ("end", None))


def test_listener_crash_costs_only_hands_free(rig):
    r = rig()
    r.turn_on()
    r.wake.gone()
    assert wait_until(lambda: not r.backend.mic_is_open)
    assert r.mode is Mode.OFF
    assert wait_until(lambda: any("rest of APLHA is fine" in n for n in r.notices))
    r.conversation.send_text("still working")
    assert wait_until(lambda: r.sessions and r.sessions[0].sent == [("text", "still working")])


def test_mic_failure_turns_hands_free_off(rig):
    r = rig()
    r.backend.fail_input = True
    r.conversation.set_hands_free(True)
    assert wait_until(lambda: any("could not be opened" in n for n in r.notices))
    assert wait_until(lambda: r.mode is Mode.OFF)


def test_needs_a_key(rig):
    r = rig(key="")
    r.conversation.set_hands_free(True)
    assert wait_until(lambda: State.ERROR in r.states)
    assert r.backend.opened_inputs == [] and r.wakes == []


def test_missing_model_is_explained(rig):
    r = rig(model_ready=False)
    r.conversation.set_hands_free(True)
    assert wait_until(lambda: any("speech model" in n for n in r.notices))
    assert r.backend.opened_inputs == [] and r.wakes == []


def test_talk_button_works_during_hands_free(rig):
    r = rig()
    r.turn_on()
    r.conversation.talk_pressed()
    assert wait_until(lambda: r.mode is Mode.TURN)
    r.sound()
    r.conversation.talk_released()
    assert wait_until(lambda: r.mode is Mode.REPLYING)
    assert r.backend.mic_is_open


def test_tracker_ends_after_silence_following_speech():
    tracker = SpeechTracker()
    assert not tracker.feed(0.4)
    ticks = 0
    while not tracker.feed(0.0):
        ticks += 1
    assert abs(ticks * CHUNK_SECONDS - SpeechTracker.SILENCE_TO_END) < 0.25


def test_tracker_gives_up_when_nobody_speaks():
    tracker = SpeechTracker()
    ticks = 0
    while not tracker.feed(0.0):
        ticks += 1
    assert abs(ticks * CHUNK_SECONDS - SpeechTracker.WAIT_FOR_SPEECH) < 0.25


def test_tracker_stops_a_turn_that_never_goes_quiet():
    tracker = SpeechTracker()
    ticks = 0
    while not tracker.feed(0.9):
        ticks += 1
    assert abs(ticks * CHUNK_SECONDS - SpeechTracker.LONGEST_TURN) < 0.25


def test_tracker_learns_steady_background_noise():
    tracker = SpeechTracker()
    assert tracker.is_speech(0.06)
    for _ in range(200):
        tracker.feed(0.03)
    assert not tracker.is_speech(0.06)
    assert tracker.is_speech(0.3)


def test_model_readiness_reads_files_only(monkeypatch, tmp_path):
    monkeypatch.setattr(wake_model, "model_dir", lambda: tmp_path / "m")
    assert not wake_model.is_ready()
    (tmp_path / "m" / "am").mkdir(parents=True)
    (tmp_path / "m" / "am" / "final.mdl").write_bytes(b"x")
    assert wake_model.is_ready()


def test_model_archive_cannot_write_outside_its_folder(tmp_path):
    import zipfile

    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../escaped.txt", "x")
    with pytest.raises(ValueError):
        wake_model._unpack(archive, tmp_path / "out")
    assert not (tmp_path / "escaped.txt").exists()


def test_data_folder_is_isolated_here():
    assert paths.data_dir() != paths.real_data_dir()
