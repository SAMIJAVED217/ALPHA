from alpha.voice.events import ReplyAudio, ReplyDone, ReplyText, ToolRequest

from .test_audio import tone
from .test_conversation import rig, wait_until  # noqa: F401


def start_reply(r):
    r.conversation.send_text("hello")
    assert wait_until(lambda: r.sessions and r.session.sent)
    played = []
    r.conversation.speaker.play = played.append
    return played


def test_the_rest_of_a_stopped_reply_is_thrown_away(rig):  # noqa: F811
    r = rig()
    played = start_reply(r)
    r.conversation.interrupt()
    assert wait_until(lambda: r.conversation._silenced)
    r.give(ReplyAudio(tone(24_000, 0.1)))
    r.give(ReplyText("more words"))
    r.give(ReplyDone())
    assert wait_until(lambda: not r.conversation._silenced)
    assert played == []


def test_the_next_message_is_heard_normally(rig):  # noqa: F811
    r = rig()
    played = start_reply(r)
    r.conversation.interrupt()
    assert wait_until(lambda: r.conversation._silenced)
    r.conversation.send_text("again")
    assert wait_until(lambda: not r.conversation._silenced)
    r.give(ReplyAudio(tone(24_000, 0.1)))
    assert wait_until(lambda: played)


def test_nothing_to_stop_changes_nothing(rig):  # noqa: F811
    r = rig()
    r.conversation.interrupt()
    r.conversation.settings_changed()
    assert not r.conversation._silenced


def test_a_tool_asked_for_in_a_stopped_reply_does_not_run(rig):  # noqa: F811
    r = rig()
    start_reply(r)
    r.conversation.interrupt()
    assert wait_until(lambda: r.conversation._silenced)
    r.give(ToolRequest("c9", "get_volume", {}))
    assert wait_until(lambda: r.session.sent[-1][0] == "tool")
    assert r.session.sent[-1][3]["ok"] is False
