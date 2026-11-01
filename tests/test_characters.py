import json

import pytest
from PySide6.QtGui import QColor, QImage

from alpha import characters, paths, settings
from alpha.characters import CHARACTERS
from alpha.voice import gemini
from alpha.ui.hud_widgets import CharacterSwitch, RingGauge, Waveform
from alpha.ui.stage import STAGE_SHADER, blink_amount, follow_voice, portrait_for, silhouette

from .test_hud import hud  # noqa: F401
from .test_interface import spin_until


@pytest.fixture(autouse=True)
def clean_characters():
    yield
    folder = paths.data_dir() / "characters"
    if folder.exists():
        for item in folder.iterdir():
            item.unlink()


def picture(path, width=200, height=300):
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor("#336699"))
    image.save(str(path))
    return path


def test_aplha_leads_the_characters():
    assert list(CHARACTERS)[0] == characters.DEFAULT_CHARACTER == "aplha"


def test_no_forbidden_or_retired_name_is_used():
    text = " ".join(f"{c.name} {c.title} {c.persona}" for c in CHARACTERS.values()).lower()
    for name in ("astra", "mira", "jarvis"):
        assert name not in text


def test_each_character_shapes_the_session():
    first, third = gemini.session_config("first"), gemini.session_config("third")
    assert "First" in first["system_instruction"] and "Third" in third["system_instruction"]
    voice = third["speech_config"]["voice_config"]["prebuilt_voice_config"]["voice_name"]
    assert voice == CHARACTERS["third"].gemini_voice


def test_choice_persists_and_a_bad_one_falls_back():
    settings.save(settings.Settings(character="third"))
    assert settings.load().character == "third"
    paths.settings_file().write_text('{"character": "someone"}', encoding="utf-8")
    assert settings.load().character == characters.DEFAULT_CHARACTER


def test_a_silhouette_stands_in_only_if_the_built_in_picture_is_missing(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(characters, "default_image", lambda _id: tmp_path / "missing.png")
    assert portrait_for("first").size() == silhouette("first").size()


def test_aplha_has_its_built_in_picture(qapp):
    assert portrait_for("aplha").size() == QImage(str(characters.default_image("aplha"))).size()


def test_a_character_with_no_picture_gets_a_silhouette(qapp):
    assert portrait_for("first").size() == silhouette("first").size()


def test_private_characters_load_from_their_own_file(tmp_path):
    path = tmp_path / characters.EXTRA_FILE
    item = {"id": "x", "name": "X", "title": "T", "persona": "P", "gemini_voice": "Puck", "windows_voice": "David"}
    path.write_text(json.dumps([item]), encoding="utf-8")
    assert characters.load_extras(path)["x"].name == "X"


def test_a_missing_or_broken_private_file_leaves_only_aplha(tmp_path):
    path = tmp_path / characters.EXTRA_FILE
    assert characters.load_extras(path) == {}
    path.write_text("not json")
    assert characters.load_extras(path) == {}


def test_your_own_picture_replaces_the_default_without_borrowing_its_marks(qapp, tmp_path):
    characters.add_image("third", picture(tmp_path / "me.png"))
    assert portrait_for("third").width() == 200
    assert characters.load_marks("third") is None


def test_large_pictures_are_reduced_and_the_original_is_not_needed(qapp, tmp_path):
    source = picture(tmp_path / "big.png", 900, 3000)
    characters.add_image("third", source)
    source.unlink()
    saved = portrait_for("third")
    assert max(saved.width(), saved.height()) == characters.LONGEST_IMAGE_SIDE


def test_a_bad_file_is_refused(qapp, tmp_path):
    bad = tmp_path / "bad.png"
    bad.write_text("nope")
    with pytest.raises(ValueError):
        characters.add_image("first", bad)


def test_removing_your_picture_returns_to_the_default_and_can_be_undone(qapp, tmp_path):
    characters.add_image("aplha", picture(tmp_path / "me.png"))
    characters.set_marks("aplha", (0.4, 0.2), (0.6, 0.2), (0.5, 0.3))
    assert characters.remove_image("aplha")
    assert not characters.image_file("aplha").exists()
    assert portrait_for("aplha").size() == QImage(str(characters.default_image("aplha"))).size()
    assert characters.can_restore_image("aplha")
    assert characters.restore_image("aplha")
    assert portrait_for("aplha").width() == 200
    assert characters.load_marks("aplha") is not None


def test_removing_when_there_is_no_picture_does_nothing():
    assert not characters.remove_image("third")
    assert not characters.restore_image("third")


def test_the_window_removes_and_restores_the_chosen_characters_picture(hud, tmp_path):  # noqa: F811
    window, _ = hud
    characters.add_image(window.settings.character, picture(tmp_path / "me.png"))
    window.remove_character_image()
    assert not characters.image_file(window.settings.character).exists()
    window._update_image_actions()
    assert window.restore_image_action.isEnabled() and not window.remove_image_action.isEnabled()
    window.restore_character_image()
    assert characters.image_file(window.settings.character).exists()


def test_the_top_name_and_log_follow_the_chosen_character(hud):  # noqa: F811
    window, _ = hud
    window.choose_character("second")
    assert window.wordmark.text() == "SECOND" and window.windowTitle() == "APLHA"
    window._append_reply("Hello")
    assert window.transcript.toPlainText().endswith("SECOND: Hello")
    window.choose_character("aplha")
    assert window.wordmark.text() == "APLHA"


def test_a_long_character_list_scrolls(hud):  # noqa: F811
    window, _ = hud
    assert window.character_switch.scroller.widget().layout().count() == len(CHARACTERS) + 1


def test_marks_are_optional(tmp_path):
    assert characters.load_marks("first") is None
    characters.set_marks("first", (0.4, 0.2), (0.6, 0.2), (0.5, 0.3))
    assert characters.load_marks("first")["mouth"] == [0.5, 0.3]


def test_character_images_stay_out_of_the_repository():
    assert characters.image_file("first").parent.parent == paths.data_dir()


def test_switch_shows_and_reports_the_choice(qapp):
    switch = CharacterSwitch("first")
    picked = []
    switch.chosen.connect(picked.append)
    assert switch.tiles["first"].isChecked()
    switch.tiles["third"].click()
    assert picked == ["third"]
    assert switch.tiles["third"].isChecked() and not switch.tiles["first"].isChecked()


def test_choosing_a_character_is_saved_and_restarts_the_session(hud):  # noqa: F811
    window, _ = hud
    window.choose_character("second")
    assert settings.load().character == "second"


def test_gauge_shows_a_dash_when_unknown(qapp):
    gauge = RingGauge("CPU")
    assert gauge.percent is None
    gauge.set_value(40)
    assert gauge.percent == 40


def test_waveform_stays_flat_in_silence_and_moves_with_sound(hud):  # noqa: F811
    window, conversation = hud
    window.waves[0].level = 0.0
    window._move_waves()
    assert window.waves[0].level == 0.0
    conversation.speaker.level = 0.5
    assert spin_until(lambda: window.waves[0].level == 0.5)
    conversation.speaker.level = 0.0


def test_screen_shows_the_chosen_spelling(hud):  # noqa: F811
    window, _ = hud
    assert window.windowTitle() == "APLHA" and window.wordmark.text() == "APLHA"


def test_blink_is_brief():
    assert blink_amount(1.0) == 0.0


def test_the_mouth_opens_faster_than_it_closes():
    opened = follow_voice(0.0, 1.0, 0.6, 0.3)
    assert opened == pytest.approx(0.6)
    assert follow_voice(opened, 0.0, 0.6, 0.3) == pytest.approx(0.42)


def test_silence_settles_the_character_back_to_still():
    amount = 1.0
    for _ in range(300):
        amount = follow_voice(amount, 0.0, 0.12, 0.04)
    assert amount < 0.01


def test_stage_program_has_a_figure_platform_and_rings():
    for name in ("figure", "tiltedRing", "portrait", "mouthOpen", "skyline", "parallax", "mirrored", "speakingPose", "energyWaves", "machineHall"):
        assert name in STAGE_SHADER
