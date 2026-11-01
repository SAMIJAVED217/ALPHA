from alpha import paths, profile
from alpha.tools.catalogue import build_toolbox
from alpha.ui.about_dialog import AboutDialog
from alpha.voice import gemini

import pytest

from .test_hud import hud  # noqa: F401


@pytest.fixture(autouse=True)
def fresh():
    (paths.data_dir() / "about_me.json").unlink(missing_ok=True)
    yield
    (paths.data_dir() / "about_me.json").unlink(missing_ok=True)


def test_answers_are_kept_on_this_computer():
    profile.save({"name": "Sami", "city": "  Seoul  ", "work": ""})
    assert profile.load() == {"name": "Sami", "city": "Seoul"}
    assert (paths.data_dir() / "about_me.json").exists()


def test_unknown_or_broken_data_is_ignored():
    profile.save({"secret_key": "x"})
    assert profile.load() == {}
    (paths.data_dir() / "about_me.json").write_text("not json")
    assert profile.load() == {}


def test_nothing_is_said_about_an_empty_profile():
    assert profile.for_the_model() == ""
    assert "owner has told you" not in gemini.session_config("aplha", "en")["system_instruction"]


def test_the_session_is_told_what_the_owner_shared():
    profile.save({"name": "Sami", "projects": "Building APLHA"})
    prompt = gemini.session_config("aplha", "en", profile.for_the_model())["system_instruction"]
    assert "Sami" in prompt and "Building APLHA" in prompt


def test_telling_aplha_something_updates_one_answer_only():
    profile.save({"name": "Sami"})
    tool = build_toolbox(None).get("update_about_me")
    plan = tool.propose({"field": "city", "text": "Seoul"})
    assert profile.load() == {"name": "Sami"}
    plan.perform()
    assert profile.load() == {"name": "Sami", "city": "Seoul"}


def test_a_made_up_field_is_refused():
    from alpha.tools.registry import ToolRefused

    with pytest.raises(ToolRefused):
        build_toolbox(None).get("update_about_me").propose({"field": "password", "text": "x"})


def test_the_form_saves_what_is_typed(qapp):
    profile.save({"name": "Sami"})
    dialog = AboutDialog()
    assert dialog.fields["name"].text() == "Sami"
    dialog.fields["work"].setPlainText("Student")
    dialog._save()
    assert profile.load()["work"] == "Student"
    dialog.deleteLater()


def test_the_more_menu_offers_it(hud):  # noqa: F811
    window, _ = hud
    assert window.about_action.text() == "About me"
