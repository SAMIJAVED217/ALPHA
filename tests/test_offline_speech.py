"""Offline speech with pretend helpers, plus the parts that need no hardware."""

import pytest

from alpha.audio.hub import AudioHub
from alpha.settings import Settings, load, save
from alpha.state import State
from alpha.voice.conversation import Conversation
from alpha.voice.events import HeardText, ReplyAudio, ReplyDone, ReplyInterrupted, ReplyText
from alpha.voice.offline_speech import take_sentences

from .fakes import FakeBackend, ScriptedSession
from .test_audio import drain, tone
from .test_interface import spin_until as wait_until

SPEECH = tone(48_000, 0.3)


class FakeSpeech:
    def __init__(self, language, on_text, on_sound, on_idle, on_exit):
        self.language = language
        self.on_text, self.on_sound, self.on_idle, self.on_exit = on_text, on_sound, on_idle, on_exit
        self.fed = []
        self.finished = 0
        self.said = []
        self.cleared = 0
        self.stopped = False
        self.pending = 0

    def feed(self, chunk):
        self.fed.append(chunk)

    def finish(self):
        self.finished += 1

    def say(self, text):
        self.said.append(text)
        self.pending += 1

    @property
    def busy(self):
        return self.pending > 0

    def clear(self):
        self.cleared += 1

    def stop(self):
        self.stopped = True

    def speak_one(self):
        self.pending -= 1
        self.on_sound(tone(24_000, 0.3))
        if self.pending == 0:
            self.on_idle()


class Rig:
    def __init__(self, engine="offline", model_ready=True):
        self.backend = FakeBackend()
        self.hub = AudioHub(lambda: self.backend)
        self.sessions = []
        self.speech = None
        self.states, self.notices, self.heard = [], [], []
        self.settings = Settings(speech_engine=engine)
        self.conversation = Conversation(
            self.settings, self.hub, connector=self._connect, load_key=lambda: "k",
            speech_factory=self._speech, speech_model_ready=lambda: model_ready,
        )
        c = self.conversation
        c.state_changed.connect(lambda state, detail: self.states.append(state))
        c.notice.connect(self.notices.append)
        c.heard.connect(self.heard.append)
        c.start()
        assert wait_until(lambda: self.states)

    async def _connect(self, key, model):
        self.sessions.append(ScriptedSession())
        return self.sessions[-1]

    def _speech(self, language, on_text, on_sound, on_idle, on_exit):
        self.speech = FakeSpeech(language, on_text, on_sound, on_idle, on_exit)
        return self.speech, self.speech

    def give(self, event):
        self.sessions[-1].give(self.conversation._loop, event)

    def talk(self):
        self.conversation.talk_pressed()
        assert wait_until(lambda: self.backend.mic_is_open)
        self.backend.feed(SPEECH)
        assert wait_until(lambda: self.speech.fed)
        self.conversation.talk_released()
        assert wait_until(lambda: self.speech.finished)

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


def test_the_default_is_still_gemini_voice(rig):
    r = rig(engine="gemini")
    assert Settings().speech_engine == "gemini"
    assert r.speech is None


def test_choice_persists_and_a_bad_value_falls_back():
    save(Settings(speech_engine="offline"))
    assert load().speech_engine == "offline"
    from alpha import paths

    paths.settings_file().write_text('{"speech_engine": "nonsense"}', encoding="utf-8")
    assert load().speech_engine == "gemini"


@pytest.mark.parametrize(
    "text,done,left",
    [
        ("Hello there", [], "Hello there"),
        ("Hello. How are", ["Hello."], "How are"),
        ("One! Two? Three. ", ["One!", "Two?", "Three."], ""),
        ("Version 2.5 is out", [], "Version 2.5 is out"),
    ],
)
def test_sentences_are_split_only_when_finished(text, done, left):
    assert take_sentences(text) == (done, left)


def test_your_sound_goes_only_to_the_local_listener(rig):
    r = rig()
    r.talk()
    r.speech.on_text("open notepad")
    assert wait_until(lambda: r.sessions and r.sessions[0].sent)
    kinds = [item[0] for item in r.sessions[0].sent]
    assert kinds == ["text"]
    assert "speech" not in kinds and "start" not in kinds
    assert r.sessions[0].sent[0][1] == "open notepad"


def test_what_was_heard_is_shown(rig):
    r = rig()
    r.talk()
    r.speech.on_text("hello there")
    assert wait_until(lambda: r.heard == ["hello there"])


def test_no_words_means_nothing_is_sent(rig):
    r = rig()
    r.talk()
    r.speech.on_text("")
    assert wait_until(lambda: any("make out" in notice for notice in r.notices))
    assert r.sessions == []
    assert wait_until(lambda: r.states[-1] is State.IDLE)


def test_no_internet_still_shows_what_was_heard_then_says_so(rig):
    r = rig()

    async def no_network(key, model):
        raise OSError("offline")

    r.conversation._connector = no_network
    r.talk()
    r.speech.on_text("are you there")
    assert wait_until(lambda: r.heard == ["are you there"])
    assert wait_until(lambda: any("internet" in notice for notice in r.notices))


def start_reply(r):
    r.conversation.send_text("hello")
    assert wait_until(lambda: r.sessions and r.sessions[-1].sent)


def test_gemini_audio_is_ignored_and_the_local_voice_speaks_the_words(rig):
    r = rig()
    start_reply(r)
    r.give(ReplyAudio(tone(24_000, 3.0)))
    r.give(ReplyText("Hello there. "))
    assert wait_until(lambda: r.speech.said == ["Hello there."])
    assert r.backend.opened_outputs == []


def test_replies_are_spoken_sentence_by_sentence_and_the_end_is_flushed(rig):
    r = rig()
    start_reply(r)
    r.give(ReplyText("One. "))
    r.give(ReplyText("Two without a stop"))
    assert wait_until(lambda: r.speech.said == ["One."])
    r.give(ReplyDone())
    assert wait_until(lambda: r.speech.said == ["One.", "Two without a stop"])


def test_state_shows_speaking_then_idle_only_after_the_sound_finishes(rig):
    r = rig()
    start_reply(r)
    r.give(ReplyText("Hello."))
    r.give(ReplyDone())
    assert wait_until(lambda: r.speech.said)
    r.speech.speak_one()
    assert wait_until(lambda: State.SPEAKING in r.states)
    assert r.states[-1] is State.SPEAKING
    drain(r.backend)
    assert wait_until(lambda: r.states[-1] is State.IDLE)


def test_reply_with_no_words_returns_to_idle(rig):
    r = rig()
    start_reply(r)
    r.give(ReplyDone())
    assert wait_until(lambda: r.states[-1] is State.IDLE)


def test_interruption_clears_the_local_voice(rig):
    r = rig()
    start_reply(r)
    r.give(ReplyText("Long sentence. "))
    assert wait_until(lambda: r.speech.said)
    r.give(ReplyInterrupted())
    assert wait_until(lambda: r.speech.cleared)


def test_pressing_talk_silences_the_local_voice(rig):
    r = rig()
    r.conversation.talk_pressed()
    assert wait_until(lambda: r.speech is not None and r.speech.cleared)
    r.conversation.talk_released()


def test_helper_crash_is_contained_and_it_restarts_next_time(rig):
    r = rig()
    assert wait_until(lambda: r.speech is not None)
    first = r.speech
    first.on_exit()
    assert wait_until(lambda: first.stopped)
    assert wait_until(lambda: any("Offline speech stopped" in notice for notice in r.notices))
    r.conversation.settings_changed()
    assert wait_until(lambda: r.speech is not first)


def test_switching_back_to_gemini_stops_the_helpers(rig):
    r = rig()
    assert wait_until(lambda: r.speech is not None)
    r.settings.speech_engine = "gemini"
    r.conversation.settings_changed()
    assert wait_until(lambda: r.speech.stopped)


def test_changing_the_language_restarts_the_listener_with_it(rig):
    r = rig()
    assert wait_until(lambda: r.speech is not None)
    first = r.speech
    assert first.language == "en"
    r.settings.speech_language = "ur"
    r.conversation.settings_changed()
    assert wait_until(lambda: r.speech is not first)
    assert first.stopped
    assert r.speech.language == "ur"


def test_a_bad_language_falls_back_to_english():
    from alpha import paths

    paths.settings_file().write_text('{"speech_language": "klingon"}', encoding="utf-8")
    assert load().speech_language == "en"


def test_missing_model_is_explained(rig):
    r = rig(model_ready=False)
    assert wait_until(lambda: any("speech model" in notice for notice in r.notices))
    assert r.speech is None


def test_gemini_mode_still_sends_real_audio(rig):
    r = rig(engine="gemini")
    r.conversation.talk_pressed()
    assert wait_until(lambda: r.backend.mic_is_open)
    r.backend.feed(SPEECH)
    assert wait_until(lambda: r.sessions and ("start", None) in r.sessions[0].sent)
    r.conversation.talk_released()
    assert wait_until(lambda: r.sessions[0].sent[-1] == ("end", None))
    r.give(HeardText("hi"))
    assert wait_until(lambda: r.heard == ["hi"])
