import asyncio
import gc

import pytest
from PySide6.QtCore import QCoreApplication, QEvent

from alpha import languages
from alpha.audio.hub import AudioHub
from alpha.settings import Settings
from alpha.state import State
from alpha.tools.catalogue import build_toolbox
from alpha.tools.registry import ToolRefused
from alpha.ui.interpreter_window import InterpreterWindow
from alpha.voice import conversation as conversation_module
from alpha.voice.conversation import Conversation
from alpha.voice.events import EndingSoon, HeardText, ReplyAudio, ReplyDone, ReplyText
from alpha.voice.gemini import INTERPRETER_MODEL, connect_interpreter, interpreter_config

from .fakes import FakeBackend, ScriptedSession
from .test_audio import tone
from .test_hands_free import FakeWake
from .test_interface import spin_until


def wait_until(condition):
    return spin_until(condition, seconds=10)

SOUND = tone(48_000, 0.1)


class Rig:
    def __init__(self, key="a-key", refusals=0):
        self.backend = FakeBackend()
        self.hub = AudioHub(lambda: self.backend)
        self.sessions, self.targets, self.states, self.notices = [], [], [], []
        self.heard, self.said, self.changes, self.normal = [], [], [], []
        self.refusals = refusals
        self.conversation = Conversation(
            Settings(), self.hub, connector=self._normal, load_key=lambda: key,
            interpreter_connector=self._connect, wake_factory=FakeWake, wake_model_ready=lambda: True,
        )
        c = self.conversation
        c.state_changed.connect(lambda state, detail: self.states.append(state))
        c.notice.connect(self.notices.append)
        c.interpreter_heard.connect(self.heard.append)
        c.interpreter_said.connect(self.said.append)
        c.interpreting_changed.connect(self.changes.append)
        c.start()
        assert wait_until(lambda: self.states)

    async def _normal(self, key, model):
        self.normal.append(ScriptedSession())
        return self.normal[-1]

    async def _connect(self, key, target):
        self.targets.append(target)
        if self.refusals:
            self.refusals -= 1
            raise OSError("no network")
        self.sessions.append(ScriptedSession())
        return self.sessions[-1]

    @property
    def session(self):
        return self.sessions[-1]

    def give(self, event):
        self.session.give(self.conversation._loop, event)

    def start(self, target="ur"):
        self.conversation.start_interpreting(target)
        assert wait_until(lambda: self.sessions and self.backend.mic_is_open)

    def sound(self, times=3):
        for _ in range(times):
            self.backend.feed(SOUND)
        self.hub.submit(lambda b: None).result(2)

    def close(self):
        self.conversation.shutdown()
        self.hub.shutdown()


@pytest.fixture
def rig(qapp, monkeypatch):
    monkeypatch.setattr(conversation_module, "INTERPRETER_RETRY_SECONDS", 0.01)
    made = []

    def make(**options):
        made.append(Rig(**options))
        return made[-1]

    yield make
    for item in made:
        item.close()


def test_the_config_uses_the_translation_model_and_stays_silent_for_its_own_language():
    config = interpreter_config("ur")
    assert config["translation_config"] == {"target_language_code": "ur", "echo_target_language": False}
    assert "system_instruction" not in config and "tools" not in config
    assert "translate" in INTERPRETER_MODEL
    assert connect_interpreter


def test_starting_opens_the_mic_and_a_session_for_that_language(rig):
    r = rig()
    r.start("ur")
    assert r.targets == ["ur"]
    assert r.conversation.interpreting and r.changes == ["ur"]
    assert wait_until(lambda: r.states[-1] is State.LISTENING)


def test_sound_goes_to_the_translator_with_no_turns(rig):
    r = rig()
    r.start()
    r.sound()
    assert wait_until(lambda: len(r.session.sent) >= 3)
    assert {item[0] for item in r.session.sent} == {"speech"}
    assert r.normal == []


def test_the_translation_is_played_and_both_sides_are_shown(rig):
    r = rig()
    r.start()
    r.give(HeardText("안녕하세요"))
    r.give(ReplyText("Hello"))
    r.give(ReplyAudio(tone(24_000, 0.2)))
    assert wait_until(lambda: r.heard == ["안녕하세요"] and r.said == ["Hello"])
    r.give(ReplyDone())
    assert wait_until(lambda: r.conversation.speaker is not None)


def test_stopping_closes_the_mic_and_the_session(rig):
    r = rig()
    r.start()
    session = r.session
    r.give(HeardText("ready"))
    assert wait_until(lambda: r.heard)
    r.conversation.stop_interpreting()
    assert wait_until(lambda: not r.backend.mic_is_open and session.closed)
    assert not r.conversation.interpreting and r.changes == ["ur", ""]


def test_esc_stops_the_translation(rig):
    r = rig()
    r.start()
    r.conversation.interrupt()
    assert wait_until(lambda: not r.conversation.interpreting)


def test_a_session_ending_is_replaced_without_losing_sound(rig):
    r = rig()
    r.start()
    first = r.session
    r.give(EndingSoon())
    assert wait_until(lambda: len(r.sessions) == 2)
    assert first.closed
    r.sound()
    assert wait_until(lambda: r.session.sent)


def test_a_dropped_connection_reconnects(rig):
    r = rig()
    r.start()
    r.give(OSError("dropped"))
    assert wait_until(lambda: len(r.sessions) == 2)
    assert wait_until(lambda: any("Reconnecting" in notice for notice in r.notices))


def test_repeated_failures_stop_and_say_why_and_close_the_mic(rig):
    r = rig(refusals=10)
    r.conversation.start_interpreting("ur")
    assert wait_until(lambda: State.ERROR in r.states)
    assert len(r.targets) == 3
    assert not r.conversation.interpreting
    assert wait_until(lambda: not r.backend.mic_is_open)


def test_without_a_key_it_says_so_and_the_mic_stays_closed(rig):
    r = rig(key="")
    r.conversation.start_interpreting("ur")
    assert wait_until(lambda: any("key" in notice for notice in r.notices))
    assert not r.conversation.interpreting and not r.backend.mic_is_open


def test_hands_free_comes_back_afterwards(rig):
    r = rig()
    r.conversation.set_hands_free(True)
    assert wait_until(lambda: r.backend.mic_is_open and r.conversation._mode.value == "waiting")
    r.start()
    assert r.conversation._mode.value == "off"
    r.conversation.stop_interpreting()
    assert wait_until(lambda: r.conversation._mode.value == "waiting" and r.backend.mic_is_open)


def test_typing_during_translation_does_not_reach_gemini(rig):
    r = rig()
    r.start()
    r.conversation.send_text("hello")
    assert wait_until(lambda: any("Stop the live translation" in notice for notice in r.notices))
    assert r.normal == []


def test_the_talk_key_does_nothing_during_translation(rig):
    r = rig()
    r.start()
    r.conversation.talk_pressed()
    r.sound()
    assert wait_until(lambda: r.session.sent)
    assert r.normal == []


# Languages.


def test_korean_english_and_urdu_are_offered_with_english_and_urdu_first():
    codes = list(languages.INTERPRETER_LANGUAGES)
    assert codes[:2] == ["en", "ur"] and "ko" in codes
    assert len(codes) >= 70


@pytest.mark.parametrize(
    "said, code",
    [("Urdu", "ur"), ("english", "en"), ("ko", "ko"), ("Chinese", "zh-Hans"), ("portuguese", "pt-BR"), ("Spanish", "es")],
)
def test_finding_a_language(said, code):
    assert languages.interpreter_code(said) == code


@pytest.mark.parametrize("said", ["Klingon", "", "  "])
def test_an_unknown_language_is_refused(said):
    assert languages.interpreter_code(said) is None


def test_the_voice_tool_checks_the_language():
    tool = build_toolbox(None).get("start_live_translation")
    assert tool.run({"language": "Urdu"})["target"] == "ur"
    with pytest.raises(ToolRefused):
        tool.run({"language": "Klingon"})


# The window.


class Window:
    def __init__(self, r):
        self.settings = Settings()
        self.view = InterpreterWindow(r.conversation, self.settings)

    def close(self):
        self.view.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        gc.collect()


def test_the_window_starts_stops_and_shows_both_sides(rig):
    r = rig()
    window = Window(r)
    try:
        window.view.open_with("ur")
        assert wait_until(lambda: r.sessions and window.view.start_button.text() == "Stop")
        assert window.settings.translate_to == "ur"
        r.give(HeardText("안녕"))
        r.give(ReplyText("Hello"))
        assert wait_until(lambda: window.view.translation.toPlainText() == "Hello")
        assert window.view.original.toPlainText() == "안녕"
        window.view.start_button.click()
        assert wait_until(lambda: window.view.start_button.text() == "Start")
        assert "closed" in window.view.status.text()
    finally:
        window.close()


def test_closing_the_window_stops_the_microphone(rig):
    r = rig()
    window = Window(r)
    try:
        window.view.open_with("en")
        assert wait_until(lambda: r.conversation.interpreting and r.backend.mic_is_open)
        window.view.close()
        assert wait_until(lambda: not r.backend.mic_is_open)
    finally:
        window.close()


def test_opening_without_a_language_waits_for_a_choice(rig):
    r = rig()
    window = Window(r)
    try:
        window.view.open_with("")
        assert not r.conversation.interpreting
    finally:
        window.close()
