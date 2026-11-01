"""Voice control of APLHA's options, the spoken keyword, and learning from the owner."""

import pytest

from alpha import keystore, lessons, profile
from alpha.tools.catalogue import build_toolbox
from alpha.tools.registry import ToolRefused
from alpha.actions import HumanPress
from alpha.voice.hands_free import Mode

from .test_computer_tools import computer, rig  # noqa: F401
from .test_hands_free import rig as ears  # noqa: F401
from .test_hands_free import waiting_action
from .test_interface import spin_until as wait_until


@pytest.fixture(autouse=True)
def clean_slate():
    """The test data folder is shared across the run, so each test starts with nothing learned."""
    lessons._file().unlink(missing_ok=True)
    profile._file().unlink(missing_ok=True)
    keystore.delete_secret(keystore.KEYWORD_ACCOUNT)
    yield
    lessons._file().unlink(missing_ok=True)
    profile._file().unlink(missing_ok=True)
    keystore.delete_secret(keystore.KEYWORD_ACCOUNT)


@pytest.fixture
def box():
    return build_toolbox(None)


# Voice control.


@pytest.mark.parametrize("action", ["fullscreen on", "Fullscreen Off", "hands free on", "hands free off", "mic off", "hide to tray"])
def test_voice_control_accepts_each_option(box, action):
    assert box.get("control_aplha").run({"action": action})["ok"] is True


def test_voice_control_refuses_anything_else(box):
    with pytest.raises(ToolRefused):
        box.get("control_aplha").run({"action": "format the disk"})


def test_a_voice_command_reaches_the_window(rig, computer):  # noqa: F811
    heard = []
    rig.conversation.aplha_command.connect(heard.append)
    rig.ask("control_aplha", action="fullscreen on")
    assert wait_until(lambda: heard == ["fullscreen on"])
    assert rig.proposals == []


# The owner's keyword.


def owner_only_action(r):
    r.turn_on()
    r.conversation.gate.propose("change your details", "About me", "x", lambda: "Changed.", owner_only=True)
    r.conversation._present(r.conversation.gate.current)
    assert r.conversation.gate.current is not None
    return r.conversation.gate.current


def test_changing_about_me_waits_for_the_owner(rig, computer):  # noqa: F811
    rig.ask("update_about_me", field="city", text="Seoul")
    assert wait_until(lambda: rig.proposals)
    assert rig.proposals[0].owner_only
    assert profile.load() == {}
    rig.conversation.confirm(HumanPress(rig.proposals[0].id))
    assert wait_until(lambda: profile.load() == {"city": "Seoul"})


@pytest.mark.parametrize("name, arguments", [("propose_remember_sensitive", {"text": "x"})])
def test_sensitive_memory_waits_for_the_owner(rig, computer, name, arguments):  # noqa: F811
    rig.ask(name, **arguments)
    assert wait_until(lambda: rig.proposals)
    assert rig.proposals[0].owner_only


def test_only_these_actions_ask(box):
    assert {name for name in box.names if box.get(name).always_asks} == {
        "propose_power_off", "update_about_me", "propose_forget", "propose_remember_sensitive",
    }


def test_the_keyword_approves_the_change(ears):  # noqa: F811
    keystore.save_secret(keystore.KEYWORD_ACCOUNT, "blue falcon")
    r = ears()
    owner_only_action(r)
    r.sound(times=4)
    r.wake.on_decision("keyword")
    assert wait_until(lambda: r.conversation.gate.current is None)


def test_saying_confirm_is_not_enough_once_a_keyword_is_set(ears):  # noqa: F811
    keystore.save_secret(keystore.KEYWORD_ACCOUNT, "blue falcon")
    r = ears()
    proposal = owner_only_action(r)
    r.sound(times=4)
    r.wake.on_decision("confirm")
    assert wait_until(lambda: any("keyword" in notice for notice in r.notices))
    assert r.conversation.gate.current is proposal


def test_anyone_can_cancel(ears):  # noqa: F811
    keystore.save_secret(keystore.KEYWORD_ACCOUNT, "blue falcon")
    r = ears()
    owner_only_action(r)
    r.sound(times=4)
    r.wake.on_decision("cancel")
    assert wait_until(lambda: r.conversation.gate.current is None)


def test_without_a_keyword_confirm_still_works(ears):  # noqa: F811
    r = ears()
    owner_only_action(r)
    r.sound(times=4)
    r.wake.on_decision("confirm")
    assert wait_until(lambda: r.conversation.gate.current is None)


def test_the_keyword_cannot_come_from_silence(ears):  # noqa: F811
    keystore.save_secret(keystore.KEYWORD_ACCOUNT, "blue falcon")
    r = ears()
    proposal = owner_only_action(r)
    r.wake.on_decision("keyword")
    r.hub.submit(lambda b: None).result(2)
    assert r.conversation.gate.current is proposal


def test_a_keyword_the_listener_cannot_hear_is_reported(ears):  # noqa: F811
    r = ears()
    r.turn_on()
    r.wake.on_decision("unknown")
    assert wait_until(lambda: any("common English" in notice for notice in r.notices))


def test_no_tool_can_read_or_set_the_keyword(box):
    assert not [name for name in box.names if "keyword" in name]


def test_the_keyword_is_not_in_what_gemini_is_told():
    from alpha.voice.gemini import session_config

    keystore.save_secret(keystore.KEYWORD_ACCOUNT, "blue falcon")
    assert "blue falcon" not in str(session_config())


# Learning.


def test_a_lesson_is_kept_and_sent_to_gemini():
    lessons.learn("Keep replies short in the morning")
    assert "Keep replies short in the morning" in lessons.for_the_model()


def test_repeating_a_lesson_strengthens_it_and_ranks_it_first():
    lessons.learn("First lesson")
    lessons.learn("Second lesson")
    lessons.learn("second LESSON")
    assert [item["text"] for item in lessons.load()] == ["Second lesson", "First lesson"]
    assert lessons.load()[0]["score"] == 2


def test_a_lesson_that_did_not_help_fades_away():
    lessons.learn("Use long answers")
    assert "Dropped" in lessons.learn("Use long answers", helped=False)
    assert lessons.load() == [] and lessons.for_the_model() == ""


def test_only_the_strongest_lessons_are_sent():
    for number in range(40):
        lessons.learn(f"Lesson {number}")
    assert lessons.for_the_model().count("- Lesson") == lessons.MOST_SENT


def test_the_tool_refuses_sensitive_details(box):
    with pytest.raises(ToolRefused):
        box.get("learn_lesson").run({"lesson": "My password is hunter2"})


def test_the_tool_learns_and_forgets(box):
    tool = box.get("learn_lesson")
    tool.run({"lesson": "Answer in Urdu at night"})
    assert lessons.load()
    tool.run({"lesson": "Answer in Urdu at night", "helped": False})
    assert lessons.load() == []


def test_a_broken_lessons_file_is_harmless():
    lessons._file().write_text("{broken", encoding="utf-8")
    assert lessons.load() == [] and lessons.for_the_model() == ""


def test_hands_free_mode_exists_for_voice_control():
    assert Mode.OFF.value == "off"
