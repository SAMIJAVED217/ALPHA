import asyncio
import time

import pytest

from alpha import paths
from alpha.actions import ActionGate, HumanPress, Outcome
from alpha.audio.hub import AudioHub
from alpha.audio.microphone import MicState
from alpha.settings import Settings
from alpha.state import State
from alpha.voice import conversation as conversation_module
from alpha.voice.conversation import Conversation, describe_failure
from alpha.voice.events import HeardText, ReplyAudio, ReplyDone, ReplyInterrupted, ReplyText, ToolRequest
from alpha.tools.catalogue import declarations
from alpha.voice.persona import PROPOSE_TOOL_NAME

from .fakes import FakeBackend, ScriptedSession
from .test_audio import drain, tone
from .test_interface import spin_until as wait_until


class Rig:
    def __init__(self, key="a-key", connect_error=None, gate=None):
        self.backend = FakeBackend()
        self.hub = AudioHub(lambda: self.backend)
        self.sessions = []
        self.connect_error = connect_error
        self.states = []
        self.notices = []
        self.replies = []
        self.heard = []
        self.proposals = []
        self.resolved = []
        self.mic_states = []
        self.checks = []
        self.conversation = Conversation(
            Settings(), self.hub, gate=gate, connector=self._connect, load_key=lambda: key
        )
        c = self.conversation
        c.state_changed.connect(lambda state, detail: self.states.append(state))
        c.notice.connect(self.notices.append)
        c.replied.connect(self.replies.append)
        c.heard.connect(self.heard.append)
        c.proposal_ready.connect(self.proposals.append)
        c.proposal_resolved.connect(self.resolved.append)
        c.mic_changed.connect(self.mic_states.append)
        c.key_checked.connect(lambda ok, text: self.checks.append((ok, text)))
        c.start()
        assert wait_until(lambda: self.states)

    async def _connect(self, key, model):
        if self.connect_error:
            raise self.connect_error
        session = ScriptedSession()
        self.sessions.append(session)
        return session

    @property
    def session(self):
        return self.sessions[-1]

    def give(self, event):
        self.session.give(self.conversation._loop, event)

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


def test_starts_idle_with_a_key_and_warns_without_one(rig):
    assert rig().states[-1] is State.IDLE
    assert rig(key="").states[-1] is State.WARNING


def test_text_needs_a_key_and_says_so(rig):
    r = rig(key="")
    r.conversation.send_text("hello")
    assert wait_until(lambda: any("key" in notice for notice in r.notices))
    assert State.ERROR in r.states
    assert r.sessions == []


def test_text_turn_goes_through_thinking_speaking_and_back_to_idle(rig):
    r = rig()
    r.conversation.send_text("hello")
    assert wait_until(lambda: r.sessions and r.session.sent == [("text", "hello")])
    assert State.THINKING in r.states
    r.give(ReplyAudio(tone(24_000, 0.3)))
    r.give(ReplyText("Hi there"))
    assert wait_until(lambda: State.SPEAKING in r.states)
    r.give(ReplyDone())
    assert wait_until(lambda: r.replies == ["Hi there"])
    assert r.states[-1] is State.SPEAKING
    drain(r.backend)
    assert wait_until(lambda: r.states[-1] is State.IDLE)
    assert r.states == [State.IDLE, State.THINKING, State.SPEAKING, State.IDLE]


def test_reply_with_no_sound_still_returns_to_idle(rig):
    r = rig()
    r.conversation.send_text("hello")
    assert wait_until(lambda: r.sessions and r.session.sent)
    r.give(ReplyDone())
    assert wait_until(lambda: r.states[-1] is State.IDLE)


def test_second_message_reuses_the_session(rig):
    r = rig()
    r.conversation.send_text("one")
    assert wait_until(lambda: r.sessions and r.session.sent)
    r.give(ReplyDone())
    r.conversation.send_text("two")
    assert wait_until(lambda: len(r.session.sent) == 2)
    assert len(r.sessions) == 1


def test_failed_connection_shows_error_then_recovers(rig, monkeypatch):
    monkeypatch.setattr(conversation_module, "ERROR_HOLD_SECONDS", 0.05)
    r = rig(connect_error=OSError("network down"))
    r.conversation.send_text("hello")
    assert wait_until(lambda: State.ERROR in r.states)
    assert wait_until(lambda: any("internet" in notice for notice in r.notices))
    assert wait_until(lambda: r.states[-1] is State.IDLE)


def test_silent_gemini_is_reported_rather_than_waited_on_for_ever(rig, monkeypatch):
    monkeypatch.setattr(conversation_module, "REPLY_TIMEOUT_SECONDS", 0.05)
    r = rig()
    r.conversation.send_text("hello")
    assert wait_until(lambda: any("did not reply" in notice for notice in r.notices))
    assert State.ERROR in r.states


def test_lost_session_during_a_reply_is_an_error_and_the_next_message_reconnects(rig):
    r = rig()
    r.conversation.send_text("hello")
    assert wait_until(lambda: r.sessions and r.session.sent)
    r.give(RuntimeError("connection dropped"))
    assert wait_until(lambda: State.ERROR in r.states)
    r.conversation.send_text("again")
    assert wait_until(lambda: len(r.sessions) == 2 and r.session.sent)


def test_no_key_means_the_mic_never_opens(rig):
    r = rig(key="")
    r.conversation.talk_pressed()
    assert wait_until(lambda: State.ERROR in r.states)
    r.conversation.talk_released()
    r.hub.submit(lambda b: None).result(2)
    assert r.backend.opened_inputs == []


def test_push_to_talk_sends_speech_only_while_held(rig):
    r = rig()
    assert r.backend.opened_inputs == []
    r.conversation.talk_pressed()
    assert wait_until(lambda: MicState.OPEN in r.mic_states)
    assert wait_until(lambda: r.states[-1] is State.LISTENING)
    assert r.sessions == []
    r.backend.feed(tone(48_000, 0.5))
    assert wait_until(lambda: r.sessions and ("start", None) in r.session.sent)
    r.conversation.talk_released()
    assert wait_until(lambda: r.mic_states[-1] is MicState.CLOSED)
    assert wait_until(lambda: r.session.sent[-1] == ("end", None))
    assert not r.backend.mic_is_open
    kinds = [item[0] for item in r.session.sent]
    assert kinds[0] == "start" and kinds[-1] == "end" and "speech" in kinds
    assert wait_until(lambda: r.states[-1] is State.THINKING)
    r.give(HeardText("hello"))
    assert wait_until(lambda: r.heard == ["hello"])


def test_a_quick_tap_with_no_sound_never_contacts_gemini(rig):
    r = rig()
    r.conversation.talk_pressed()
    assert wait_until(lambda: r.backend.mic_is_open)
    r.conversation.talk_released()
    assert wait_until(lambda: any("No sound" in notice for notice in r.notices))
    assert wait_until(lambda: r.states[-1] is State.IDLE)
    assert r.sessions == []


def test_mic_failure_shows_an_error_not_a_listening_state(rig):
    r = rig()
    r.backend.fail_input = True
    r.conversation.talk_pressed()
    assert wait_until(lambda: State.ERROR in r.states)
    assert State.LISTENING not in r.states


def test_pressing_talk_during_a_reply_stops_the_reply(rig):
    r = rig()
    r.conversation.send_text("hello")
    assert wait_until(lambda: r.sessions and r.session.sent)
    r.give(ReplyAudio(tone(24_000, 3.0)))
    assert wait_until(lambda: State.SPEAKING in r.states)
    r.conversation.talk_pressed()
    assert wait_until(lambda: r.backend.mic_is_open)
    assert len(r.conversation.speaker._buffer) == 0
    r.conversation.talk_released()


def test_server_interruption_clears_playback(rig):
    r = rig()
    r.conversation.send_text("hello")
    assert wait_until(lambda: r.sessions and r.session.sent)
    r.give(ReplyAudio(tone(24_000, 3.0)))
    assert wait_until(lambda: State.SPEAKING in r.states)
    r.give(ReplyInterrupted())
    assert wait_until(lambda: len(r.conversation.speaker._buffer) == 0)


def test_no_offered_tool_can_decide_a_proposal():
    declared = [f["name"] for tool in declarations() for f in tool["function_declarations"]]
    assert PROPOSE_TOOL_NAME in declared
    for name in declared:
        assert not any(word in name for word in ("confirm", "approve", "decide", "accept", "run_", "shell"))


@pytest.mark.parametrize(
    "name", ["confirm", "confirm_action", "approve", "press_confirm", "create_file", "run_command", ""]
)
def test_the_model_cannot_call_anything_else(rig, name):
    r = rig()
    r.conversation.send_text("hello")
    assert wait_until(lambda: r.sessions and r.session.sent)
    r.give(ToolRequest("c1", name, {"file_name": "x.txt", "content": "y", "proposal_id": "z"}))
    assert wait_until(lambda: len(r.session.sent) == 2)
    assert r.session.sent[1][3] == {"error": "That tool does not exist."}
    assert r.proposals == []
    assert list(paths.created_files_dir().iterdir()) == []


def test_model_proposal_waits_and_cannot_approve_itself(rig):
    r = rig()
    r.conversation.send_text("hello")
    assert wait_until(lambda: r.sessions and r.session.sent)
    r.conversation._outside_text_seen = True
    r.give(
        ToolRequest(
            "c1",
            PROPOSE_TOOL_NAME,
            {"file_name": "a.txt", "content": "text", "confirmed": True, "approved": True},
        )
    )
    assert wait_until(lambda: r.proposals)
    assert wait_until(lambda: r.states[-1] is State.WARNING)
    assert list(paths.created_files_dir().iterdir()) == []
    assert not any(item[0] == "tool" for item in r.session.sent)


def test_model_gets_the_true_result_after_a_person_confirms(rig):
    r = rig()
    r.conversation.send_text("hello")
    assert wait_until(lambda: r.sessions and r.session.sent)
    r.conversation._outside_text_seen = True
    r.give(ToolRequest("c1", PROPOSE_TOOL_NAME, {"file_name": "a.txt", "content": "text"}))
    assert wait_until(lambda: r.proposals)
    r.conversation.confirm(HumanPress(r.proposals[0].id))
    assert wait_until(lambda: r.resolved)
    assert r.resolved[0].outcome is Outcome.CREATED
    assert wait_until(lambda: r.session.sent[-1][0] == "tool")
    assert r.session.sent[-1][1] == "c1"
    assert r.session.sent[-1][3]["created"] is True
    assert (paths.created_files_dir() / "a.txt").read_text() == "text"


def test_model_is_told_when_the_person_cancels(rig):
    r = rig()
    r.conversation.send_text("hello")
    assert wait_until(lambda: r.sessions and r.session.sent)
    r.conversation._outside_text_seen = True
    r.give(ToolRequest("c1", PROPOSE_TOOL_NAME, {"file_name": "a.txt", "content": "text"}))
    assert wait_until(lambda: r.proposals)
    r.conversation.cancel(r.proposals[0].id)
    assert wait_until(lambda: r.session.sent[-1][0] == "tool")
    assert r.session.sent[-1][3]["created"] is False
    assert list(paths.created_files_dir().iterdir()) == []


def test_real_time_expiry_tells_the_model_and_creates_nothing(rig):
    r = rig(gate=ActionGate(expiry_seconds=0.1))
    r.conversation.send_text("hello")
    assert wait_until(lambda: r.sessions and r.session.sent)
    r.conversation._outside_text_seen = True
    r.give(ToolRequest("c1", PROPOSE_TOOL_NAME, {"file_name": "a.txt", "content": "text"}))
    assert wait_until(lambda: r.resolved)
    assert r.resolved[0].outcome is Outcome.EXPIRED
    assert wait_until(lambda: r.session.sent[-1][0] == "tool")
    assert r.session.sent[-1][3]["created"] is False
    assert list(paths.created_files_dir().iterdir()) == []


def test_a_plain_request_for_a_file_needs_no_press(rig):
    r = rig()
    r.conversation.send_text("hello")
    assert wait_until(lambda: r.sessions and r.session.sent)
    r.give(ToolRequest("c1", PROPOSE_TOOL_NAME, {"file_name": "a.txt", "content": "text"}))
    assert wait_until(lambda: r.session.sent[-1][0] == "tool")
    assert r.proposals == []
    assert r.session.sent[-1][3]["created"] is True
    assert (paths.created_files_dir() / "a.txt").read_text() == "text"


def test_refused_model_proposal_is_reported_back_at_once(rig):
    r = rig()
    r.conversation.send_text("hello")
    assert wait_until(lambda: r.sessions and r.session.sent)
    r.give(ToolRequest("c1", PROPOSE_TOOL_NAME, {"file_name": "..\\evil.exe", "content": "text"}))
    assert wait_until(lambda: r.session.sent[-1][0] == "tool")
    assert r.session.sent[-1][3]["created"] is False
    assert r.proposals == []


def test_local_file_command_uses_the_same_gate(rig):
    r = rig(key="")
    r.conversation.propose_file("mine.txt", "by hand")
    assert wait_until(lambda: r.proposals)
    assert wait_until(lambda: r.states[-1] is State.WARNING)
    assert list(paths.created_files_dir().iterdir()) == []
    r.conversation.confirm(HumanPress(r.proposals[0].id))
    assert wait_until(lambda: r.resolved)
    assert (paths.created_files_dir() / "mine.txt").read_text() == "by hand"


def test_key_check_reports_what_gemini_said(rig):
    r = rig()
    r.conversation.check_key("k", "m")
    assert wait_until(lambda: r.checks == [(True, "Gemini accepted the key.")])
    assert r.sessions[0].closed
    bad = rig(connect_error=RuntimeError("API key not valid"))
    bad.conversation.check_key("k", "m")
    assert wait_until(lambda: bad.checks and bad.checks[0][0] is False)
    assert "did not accept" in bad.checks[0][1]


def test_failure_messages_never_contain_the_key():
    message = describe_failure(ValueError("boom with secret-abc inside"), "secret-abc")
    assert "secret-abc" not in message
    assert "[key]" in message


@pytest.mark.parametrize(
    "error,words",
    [
        (RuntimeError("API key not valid"), "did not accept"),
        (RuntimeError("models/x is not found"), "does not offer"),
        (RuntimeError("429 RESOURCE_EXHAUSTED"), "limit"),
        (asyncio.TimeoutError(), "in time"),
        (OSError("refused"), "internet"),
    ],
)
def test_failures_read_as_plain_language(error, words):
    assert words in describe_failure(error)


def test_conversation_does_not_import_the_sdk_on_the_calling_thread():
    import subprocess
    import sys

    script = (
        "import sys\n"
        "import alpha.voice.conversation\n"
        "print('google.genai' in sys.modules, 'sounddevice' in sys.modules)\n"
    )
    done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True)
    assert done.stdout.split() == ["False", "False"]
    time.sleep(0)
