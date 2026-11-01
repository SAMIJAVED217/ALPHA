import pytest

from alpha import languages, settings
from alpha.settings import SPEECH_LANGUAGES, Settings
from alpha.voice import gemini
from alpha.voice.events import ReplyDone, ReplyText
from alpha.state import State

from .test_hud import hud  # noqa: F401
from .test_offline_speech import rig, wait_until  # noqa: F401

VOICES = {"en": "Microsoft David Desktop", "ko": "Microsoft Heami Desktop"}


@pytest.fixture(autouse=True)
def laptop_voices(monkeypatch):
    monkeypatch.setattr(languages, "installed_voices", lambda: dict(VOICES))


def test_every_language_is_one_the_offline_model_can_hear():
    from faster_whisper.tokenizer import _LANGUAGE_CODES

    assert set(languages.NAMES) == set(_LANGUAGE_CODES)


def test_common_choices_stay_on_top_and_the_rest_are_listed(hud):  # noqa: F811
    window, _ = hud
    assert set(languages.MAIN_CHOICES) == {"en", "ko", "ur", "hi", "auto"}
    window.language_actions["ja"].trigger()
    assert settings.load().speech_language == "ja"


def test_any_language_can_be_instructed():
    for code, name in languages.NAMES.items():
        assert f"Reply in {name}" in languages.instruction(code)


@pytest.mark.parametrize(
    "words, code",
    [
        ("\u3053\u3093\u306b\u3061\u306f\u4e16\u754c", "ja"),
        ("\u4f60\u597d", "zh"),
        ("\u041f\u0440\u0438\u0432\u0435\u0442", "ru"),
        ("\u0645\u0631\u062d\u0628\u0627", "ar"),
        ("\u0e2a\u0e27\u0e31\u0e2a\u0e14\u0e35", "th"),
    ],
)
def test_other_scripts_are_recognised(words, code):
    assert languages.language_of(words) == code


def test_english_korean_urdu_and_hindi_are_all_offered():
    assert list(SPEECH_LANGUAGES)[:4] == ["en", "ko", "ur", "hi"]


@pytest.mark.parametrize("code, name", [("en", "English"), ("ko", "Korean"), ("ur", "Urdu"), ("hi", "Hindi")])
def test_the_session_is_told_the_language(code, name):
    assert f"Reply in {name}" in gemini.session_config("aplha", code)["system_instruction"]


def test_matching_the_owner_follows_their_speech():
    assert "whichever language" in gemini.session_config("aplha", "auto")["system_instruction"]


def test_the_choice_persists():
    settings.save(Settings(speech_language="ko"))
    assert settings.load().speech_language == "ko"


@pytest.mark.parametrize(
    "text, code",
    [("hello", "en"), ("안녕하세요", "ko"), ("نمستے", "ur"),
     ("नमस्ते", "hi"), ("ok 안녕", "ko")],
)
def test_a_reply_is_recognised_by_its_script(text, code):
    assert languages.language_of(text) == code


def test_availability_tells_the_truth_about_this_computer():
    assert "Heami" in languages.availability("ko")
    assert "no Hindi voice" in languages.availability("hi")
    assert "no Urdu voice" in languages.availability("ur")


def start_reply(r, language):
    r.settings.speech_language = language
    assert wait_until(lambda: r.speech is not None)
    r.conversation.send_text("hello")
    assert wait_until(lambda: r.sessions and r.sessions[-1].sent)


def test_korean_replies_use_the_korean_windows_voice(rig):  # noqa: F811
    r = rig()
    start_reply(r, "ko")
    r.give(ReplyText("안녕하세요. "))
    r.give(ReplyDone())
    assert wait_until(lambda: r.speech.said)
    assert r.speech.voice == VOICES["ko"]


def test_with_no_windows_voice_the_reply_stays_as_text_and_says_why(rig):  # noqa: F811
    r = rig()
    start_reply(r, "hi")
    r.give(ReplyText("नमस्ते। "))
    r.give(ReplyText("दोस्त। "))
    r.give(ReplyDone())
    assert wait_until(lambda: r.states[-1] is State.IDLE)
    assert r.speech.said == []
    assert len([notice for notice in r.notices if "no Hindi voice" in notice]) == 1


def test_matching_the_owner_picks_the_voice_for_each_reply(rig):  # noqa: F811
    r = rig()
    start_reply(r, "auto")
    r.give(ReplyText("안녕하세요. "))
    r.give(ReplyDone())
    assert wait_until(lambda: r.speech.said)
    assert r.speech.voice == VOICES["ko"]


def test_english_keeps_each_characters_own_voice(rig):  # noqa: F811
    r = rig()
    start_reply(r, "en")
    r.give(ReplyText("Hello there. "))
    r.give(ReplyDone())
    assert wait_until(lambda: r.speech.said)
    assert r.speech.voice in ("David", "Zira")


def test_the_menu_changes_the_language_and_saves_it(hud):  # noqa: F811
    window, _ = hud
    window.language_actions["ur"].trigger()
    assert window.settings.speech_language == "ur"
    assert settings.load().speech_language == "ur"
    assert window.language_actions["ur"].isChecked() and not window.language_actions["en"].isChecked()
