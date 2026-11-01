import httpx
import pytest

from alpha import paths
from alpha.tools.catalogue import build_toolbox
from alpha.tools.registry import ToolRefused
from alpha.tools.watch_tools import watch_tools
from alpha.watchdog import SHORTEST_LINE, Found, Watch, WatchBoard, WatchProblem, fetch_page, lines_of_page, new_matches

from .test_hud import hud  # noqa: F401
from .test_interface import spin_until


@pytest.fixture(autouse=True)
def no_saved_watches():
    saved = paths.data_dir() / "watches.json"
    saved.unlink(missing_ok=True)
    yield
    saved.unlink(missing_ok=True)


def board_reading(lines):
    return WatchBoard(lambda source: list(lines), lambda source: list(lines))


def test_the_first_look_only_learns_what_is_there():
    watch = Watch("Portal", "x", ["exam"])
    assert new_matches(watch, ["The exam is on Monday"]) == []
    assert watch.started


def test_a_new_matching_line_is_found_once():
    watch = Watch("Portal", "x", ["exam"])
    new_matches(watch, ["Old notice about nothing"])
    assert new_matches(watch, ["Midterm EXAM moved to Friday"]) == ["Midterm EXAM moved to Friday"]
    assert new_matches(watch, ["Midterm EXAM moved to Friday"]) == []


def test_new_lines_that_do_not_matter_stay_quiet():
    watch = Watch("Portal", "x", ["exam"])
    new_matches(watch, [])
    assert new_matches(watch, ["Cafeteria closed on Monday"]) == []


def test_very_short_lines_are_ignored():
    watch = Watch("Portal", "x", ["e"])
    new_matches(watch, [])
    assert new_matches(watch, ["e" * (SHORTEST_LINE - 1)]) == []


def test_pages_become_plain_lines_without_hidden_parts():
    page = "<html><script>exam()</script><p>Exam &amp; quiz</p><div>Second   line</div></html>"
    assert lines_of_page(page) == ["Exam & quiz", "Second line"]


def fetched(status=200, body="<p>Hello there world</p>"):
    return fetch_page("https://x.test", httpx.MockTransport(lambda r: httpx.Response(status, text=body)))


def test_a_public_page_is_read():
    assert fetched() == ["Hello there world"]


def test_a_sign_in_page_says_to_watch_the_window():
    with pytest.raises(WatchProblem, match="signing in"):
        fetched(body='<form><input type="password"></form>')


def test_a_site_error_is_explained():
    with pytest.raises(WatchProblem):
        fetched(status=500)


def test_watches_survive_a_restart():
    first = board_reading([])
    first.add("Portal", "https://x.test", ["exam"])
    assert board_reading([]).watches["Portal"].looking_for == ["exam"]


def test_a_broken_watch_file_starts_empty():
    (paths.data_dir() / "watches.json").write_text("not json")
    assert board_reading([]).watches == {}


def test_check_all_reports_new_matches():
    lines = ["Nothing to see here"]
    board = board_reading(lines)
    board.add("Portal", "My Portal - Chrome", ["exam"])
    assert board.check_all() == []
    lines.append("Exam timetable published")
    found = board.check_all()
    assert found[0].lines == ("Exam timetable published",)


def test_web_addresses_are_fetched_and_windows_are_read():
    used = []
    board = WatchBoard(lambda s: used.append("window") or [], lambda s: used.append("web") or [])
    board.add("A", "https://x.test", ["e"])
    board.add("B", "Portal", ["e"])
    board.check_all()
    assert used == ["web", "window"]


def test_a_problem_is_reported_once():
    def fail(_source):
        raise WatchProblem("No window")

    board = WatchBoard(fail, fail)
    board.add("Portal", "x", ["exam"])
    assert board.check_all()[0].problem == "No window"
    assert board.check_all() == []


def test_the_tools_add_list_and_stop():
    tools = {tool.name: tool for tool in watch_tools(board_reading([]))}
    tools["watch_page"].run({"name": "Portal", "source": "x", "looking_for": "exam, my section "})
    assert tools["list_watches"].run({})["watches"][0]["looking_for"] == ["exam", "my section"]
    tools["stop_watching"].run({"name": "Portal"})
    with pytest.raises(ToolRefused):
        tools["stop_watching"].run({"name": "Portal"})


def test_no_watch_tool_returns_page_text_to_the_model():
    box = build_toolbox(None)
    for name in ("watch_page", "list_watches", "stop_watching"):
        assert not box.get(name).needs_confirmation


def test_a_match_reaches_the_owner_in_the_log_and_never_the_model(hud):  # noqa: F811
    window, conversation = hud
    conversation.watch_found.emit(Found("Portal", ("Exam timetable published",)))
    assert spin_until(lambda: "Exam timetable published" in window.transcript.toPlainText())
    assert conversation._session is None


def test_a_problem_is_shown_too(hud):  # noqa: F811
    window, conversation = hud
    conversation.watch_found.emit(Found("Portal", problem="No window"))
    assert spin_until(lambda: "No window" in window.transcript.toPlainText())
