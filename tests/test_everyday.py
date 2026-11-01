from datetime import datetime
from pathlib import Path

import httpx
import pytest

from alpha import autostart, settings
from alpha.reminders import ReminderBook
from alpha.tools import files as files_module
from alpha.tools.everyday import WebInfo, everyday_tools
from alpha.tools.files import file_tools
from alpha.tools.registry import ToolRefused, UndoStack

from .test_hud import hud  # noqa: F401
from .test_interface import spin_until

NOW = datetime(2026, 11, 1, 9, 30)


class Clipboard:
    text = "copied words"

    def clipboard_text(self):
        return self.text

    def set_clipboard(self, value):
        self.text = value

    def recycle(self, path):
        path.unlink()


def everyday(transport=None, computer=None):
    book = ReminderBook()
    tools = everyday_tools(book, WebInfo(transport), computer or Clipboard(), lambda: NOW)
    return book, {tool.name: tool for tool in tools}


@pytest.fixture(autouse=True)
def no_saved_reminders():
    from alpha import paths

    (paths.data_dir() / "reminders.json").unlink(missing_ok=True)
    yield
    (paths.data_dir() / "reminders.json").unlink(missing_ok=True)


def test_the_time_is_read_from_the_clock():
    assert "1 November 2026, 09:30" in everyday()[1]["get_time"].run({})["local_time"]


def test_a_reminder_in_minutes_comes_due_and_is_taken_once():
    book, tools = everyday()
    tools["set_reminder"].run({"text": "Call home", "in_minutes": 10})
    assert book.take_due(datetime(2026, 11, 1, 9, 39)) == []
    due = book.take_due(datetime(2026, 11, 1, 9, 40))
    assert [item.text for item in due] == ["Call home"]
    assert book.take_due(datetime(2026, 11, 1, 9, 41)) == []


def test_a_reminder_at_a_clock_time_survives_a_restart():
    _, tools = everyday()
    tools["set_reminder"].run({"text": "Exam", "at": "2026-11-02 08:00"})
    assert [item.text for item in ReminderBook().upcoming()] == ["Exam"]


@pytest.mark.parametrize("when", [{"at": "2020-01-01 08:00"}, {"at": "tomorrow"}, {"in_minutes": 0}, {}])
def test_bad_times_are_refused(when):
    with pytest.raises(ToolRefused):
        everyday()[1]["set_reminder"].run({"text": "x", **when})


def test_reminders_can_be_listed_and_cancelled():
    _, tools = everyday()
    saved = tools["set_reminder"].run({"text": "x", "in_minutes": 5})
    assert len(tools["list_reminders"].run({})["reminders"]) == 1
    tools["cancel_reminder"].run({"id": saved["id"]})
    assert tools["list_reminders"].run({})["reminders"] == []
    with pytest.raises(ToolRefused):
        tools["cancel_reminder"].run({"id": saved["id"]})


def fake_web(request):
    if "geocoding" in str(request.url):
        return httpx.Response(200, json={"results": [{"name": "Seoul", "country": "South Korea", "latitude": 1, "longitude": 2}]})
    if "forecast" in str(request.url):
        return httpx.Response(200, json={
            "current": {"temperature_2m": 9, "apparent_temperature": 7, "weather_code": 61, "wind_speed_10m": 12},
            "daily": {"temperature_2m_max": [12], "temperature_2m_min": [4], "precipitation_probability_max": [60]},
        })
    return httpx.Response(200, text="<rss><channel><item><title>One</title></item><item><title>Two</title></item></channel></rss>")


def test_weather_is_read_from_the_service():
    report = everyday(httpx.MockTransport(fake_web))[1]["get_weather"].run({"city": "Seoul"})
    assert report["sky"] == "light rain" and report["high_c"] == 12 and "Seoul" in report["place"]


def test_an_unknown_place_is_refused():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    with pytest.raises(ToolRefused):
        everyday(transport)[1]["get_weather"].run({"city": "Nowhere"})


def test_headlines_are_read_and_marked_as_outside_text():
    tool = everyday(httpx.MockTransport(fake_web))[1]["get_news"]
    assert tool.run({})["untrusted_headlines"] == ["One", "Two"]


def test_a_broken_feed_is_refused():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text="not xml"))
    with pytest.raises(ToolRefused):
        everyday(transport)[1]["get_news"].run({})


def test_the_clipboard_is_read_and_written():
    computer = Clipboard()
    tools = everyday(computer=computer)[1]
    assert tools["read_clipboard"].run({})["untrusted_clipboard"] == "copied words"
    tools["write_clipboard"].run({"text": "fixed"})
    assert computer.text == "fixed"


def test_an_empty_clipboard_says_so():
    computer = Clipboard()
    computer.text = ""
    with pytest.raises(ToolRefused):
        everyday(computer=computer)[1]["read_clipboard"].run({})


@pytest.fixture
def desk(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(files_module, "_OFF_LIMITS_NAMES", {"windows"})
    (tmp_path / "Desktop").mkdir()
    (tmp_path / "Documents").mkdir()
    (tmp_path / "Desktop" / "notes.txt").write_text("hello")
    (tmp_path / "Desktop" / "photo.jpg").write_bytes(b"x")
    undo = UndoStack()
    return tmp_path, undo, {tool.name: tool for tool in file_tools(Clipboard(), undo)}


def run_plan(tools, name, **arguments):
    return tools[name].propose(arguments).perform()


def test_folders_can_be_named_simply(desk):
    _, _, tools = desk
    names = [item["name"] for item in tools["list_folder"].run({"folder": "Desktop"})["items"]]
    assert names == ["notes.txt", "photo.jpg"]


def test_files_can_be_found_and_read(desk):
    _, _, tools = desk
    assert tools["find_files"].run({"folder": "Desktop", "name": "NOTES"})["found"]
    assert tools["read_file"].run({"path": "Desktop/notes.txt"})["untrusted_file_contents"] == "hello"


def test_moving_can_be_undone(desk):
    home, undo, tools = desk
    run_plan(tools, "propose_move_file", source="Desktop/notes.txt", destination_folder="Documents")
    assert (home / "Documents" / "notes.txt").exists()
    undo.undo()
    assert (home / "Desktop" / "notes.txt").exists()


def test_renaming_copying_and_new_folders_can_be_undone(desk):
    home, undo, tools = desk
    run_plan(tools, "propose_rename_file", path="Desktop/notes.txt", new_name="plan.txt")
    undo.undo()
    assert (home / "Desktop" / "notes.txt").exists()
    run_plan(tools, "propose_copy_file", source="Desktop/notes.txt", destination_folder="Documents")
    undo.undo()
    assert not (home / "Documents" / "notes.txt").exists()
    run_plan(tools, "propose_create_folder", path="Documents/New")
    undo.undo()
    assert not (home / "Documents" / "New").exists()


def test_nothing_is_ever_overwritten(desk):
    home, _, tools = desk
    (home / "Documents" / "notes.txt").write_text("keep me")
    with pytest.raises(ToolRefused):
        tools["propose_move_file"].propose({"source": "Desktop/notes.txt", "destination_folder": "Documents"})
    assert (home / "Documents" / "notes.txt").read_text() == "keep me"


def test_organising_sorts_by_type_and_undoes_in_one_step(desk):
    home, undo, tools = desk
    (home / "Desktop" / "keep.lnk").write_text("x")
    run_plan(tools, "propose_organise_folder", folder="Desktop")
    assert (home / "Desktop" / "Documents" / "notes.txt").exists()
    assert (home / "Desktop" / "Pictures" / "photo.jpg").exists()
    assert (home / "Desktop" / "keep.lnk").exists()
    undo.undo()
    assert (home / "Desktop" / "notes.txt").exists()
    assert not (home / "Desktop" / "Pictures").exists()


@pytest.mark.parametrize(
    "where",
    [r"C:\Windows\System32", r"C:\Program Files\x",
     str(Path(files_module.__file__)), r"C:\Users\me\AppData\Roaming\x.txt", r"C:\Users\me\.ssh\id_rsa"],
)
def test_protected_places_are_off_limits(where):
    tools = {tool.name: tool for tool in file_tools(Clipboard(), UndoStack())}
    with pytest.raises(ToolRefused, match="off limits"):
        tools["read_file"].run({"path": where})


def test_the_owner_can_put_their_own_folders_off_limits(tmp_path):
    from alpha import paths

    secret = tmp_path / "private"
    secret.mkdir()
    (secret / "a.txt").write_text("x")
    listing = paths.data_dir() / "off_limits.txt"
    listing.write_text(str(secret), encoding="utf-8")
    tools = {tool.name: tool for tool in file_tools(Clipboard(), UndoStack())}
    with pytest.raises(ToolRefused, match="off limits"):
        tools["read_file"].run({"path": str(secret / "a.txt")})
    listing.unlink()


def test_deleting_goes_to_the_recycle_bin_and_is_checked(desk):
    home, _, tools = desk
    assert "Recycle Bin" in run_plan(tools, "propose_delete_file", path="Desktop/notes.txt")
    assert not (home / "Desktop" / "notes.txt").exists()


def test_start_with_windows_can_be_turned_off(tmp_path, monkeypatch):
    link = tmp_path / "APLHA.lnk"
    monkeypatch.setattr(autostart, "startup_link", lambda: link)
    assert not autostart.is_on()
    link.write_text("x")
    assert autostart.is_on()
    autostart.turn_off()
    assert not autostart.is_on()


def test_a_due_reminder_reaches_the_window(hud):  # noqa: F811
    window, conversation = hud
    conversation.reminder_due.emit("Call home")
    assert spin_until(lambda: "Call home" in window.transcript.toPlainText())


def test_the_briefing_runs_once_a_day(hud):  # noqa: F811
    window, conversation = hud
    asked = []
    conversation.give_briefing = lambda: asked.append(1)
    window.settings.setup_complete = True
    window.maybe_brief(datetime(2026, 11, 1, 8, 0))
    window.maybe_brief(datetime(2026, 11, 1, 9, 0))
    assert asked == [1]
    window.maybe_brief(datetime(2026, 11, 2, 8, 0))
    assert asked == [1, 1]
    assert settings.load().last_briefing == "2026-11-02"


def test_the_briefing_can_be_switched_off(hud):  # noqa: F811
    window, conversation = hud
    asked = []
    conversation.give_briefing = lambda: asked.append(1)
    window.settings.setup_complete = True
    window.settings.morning_briefing = False
    window.maybe_brief(datetime(2026, 11, 1, 8, 0))
    assert asked == []
