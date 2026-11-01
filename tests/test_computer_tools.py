"""Computer tools against a pretend computer. Nothing here touches the real one."""

from pathlib import Path

import pytest

from alpha.actions import ActionGate, HumanPress, Outcome
from alpha.audio.hub import AudioHub
from alpha.settings import Settings
from alpha.state import State
from alpha.tools import computer as computer_module
from alpha.tools.computer import skip_ad
from alpha.voice import conversation as conversation_module
from alpha.tools.catalogue import build_toolbox
from alpha.tools.registry import Plan, ToolRefused
from alpha.voice.conversation import Conversation
from alpha.voice.events import ToolRequest

from .fakes import FakeBackend, ScriptedSession
from .test_interface import spin_until as wait_until


class FakeComputer:
    def __init__(self):
        self.percent = 40
        self.muted = False
        self.bright = 70
        self.stuck_volume = False
        self.no_brightness = False
        self.open_windows = {1: "Untitled - Notepad", 2: "Budget.xlsx - Excel", 3: "Report - Word"}
        self.log = []
        self.focus_works = True
        self.close_works = True
        self.new_window_on_open = True
        self.front = None

    def in_front(self, handle):
        return handle == self.front

    def volume(self):
        return self.percent, self.muted

    def power(self, action):
        self.log.append(("power", action))

    def lock(self):
        self.log.append(("lock",))

    def sleep(self):
        self.log.append(("sleep",))

    def set_volume(self, percent):
        if not self.stuck_volume:
            self.percent = percent

    def set_mute(self, muted):
        self.muted = muted

    def brightness(self):
        if self.no_brightness:
            raise ToolRefused("This screen does not let Windows change its brightness.")
        return self.bright

    def set_brightness(self, percent):
        self.bright = percent

    def apps(self):
        return {"Notepad": Path("notepad.lnk"), "Google Chrome": Path("chrome.lnk"), "Paint": Path("paint.lnk")}

    def open_shortcut(self, link):
        self.log.append(("open", link))
        if self.new_window_on_open:
            self.open_windows[9] = "New app"

    def windows(self):
        return dict(self.open_windows)

    def show_window(self, handle, how):
        self.log.append((how, handle))

    def focus_window(self, handle):
        self.log.append(("focus", handle))
        return self.focus_works

    def close_window(self, handle):
        self.log.append(("close", handle))
        return self.close_works

    def type_text(self, text):
        self.log.append(("type", text))

    def press_keys(self, names):
        self.log.append(("keys", names))

    def screen_size(self):
        return 1920, 1080

    def click(self, x, y, right):
        self.log.append(("click", x, y, right))


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    monkeypatch.setattr(computer_module, "APP_WAIT_SECONDS", 0.0)


@pytest.fixture
def computer():
    return FakeComputer()


@pytest.fixture
def box(computer):
    return build_toolbox(computer)


def run(box, tool_name, /, **arguments):
    return box.get(tool_name).run(arguments)


def plan(box, tool_name, /, **arguments):
    return box.get(tool_name).propose(arguments)


def test_set_volume_reports_what_it_reads_back(box, computer):
    assert run(box, "set_volume", percent=65)["percent"] == 65
    assert computer.percent == 65


def test_volume_that_did_not_change_is_a_failure_not_a_success(box, computer):
    computer.stuck_volume = True
    with pytest.raises(ToolRefused):
        run(box, "set_volume", percent=65)


@pytest.mark.parametrize("value", [-1, 101, "50", None, True, 12.5])
def test_bad_volume_is_refused(box, computer, value):
    with pytest.raises(ToolRefused):
        run(box, "set_volume", percent=value)
    assert computer.percent == 40


def test_mute(box, computer):
    assert run(box, "set_mute", muted=True)["muted"] is True
    assert computer.muted


def test_volume_mentions_still_muted(box, computer):
    computer.muted = True
    assert "muted" in run(box, "set_volume", percent=50)["detail"]


def test_brightness(box, computer):
    assert run(box, "set_brightness", percent=30)["percent"] == 30


def test_screen_without_brightness_control_is_reported(box, computer):
    computer.no_brightness = True
    with pytest.raises(ToolRefused):
        run(box, "get_brightness")


def test_open_app_by_partial_name_and_seen_window(box, computer):
    result = run(box, "open_app", name="chrome")
    assert result["opened"] == "Google Chrome"
    assert "New app" in result["detail"]
    assert computer.log == [("open", Path("chrome.lnk"))]


def test_open_app_without_a_window_does_not_claim_success(box, computer):
    computer.new_window_on_open = False
    result = run(box, "open_app", name="paint")
    assert result["window_seen"] is False
    assert "no new window" in result["detail"]


def test_unknown_app_opens_nothing_and_offers_close_names(box, computer):
    with pytest.raises(ToolRefused) as refused:
        run(box, "open_app", name="notepd")
    assert "Notepad" in str(refused.value)
    assert computer.log == []


def test_app_names_are_never_treated_as_paths_or_commands(box, computer):
    for name in ("C:\\Windows\\System32\\cmd.exe", "powershell -c evil", "&& del *"):
        with pytest.raises(ToolRefused):
            run(box, "open_app", name=name)
    assert computer.log == []


def test_list_and_focus_windows(box, computer):
    assert "Budget.xlsx - Excel" in run(box, "list_windows")["windows"]
    run(box, "focus_window", window="excel")
    assert computer.log == [("focus", 2)]


def test_focus_failure_is_reported(box, computer):
    computer.focus_works = False
    with pytest.raises(ToolRefused):
        run(box, "focus_window", window="excel")


def test_ambiguous_window_is_refused(box, computer):
    computer.open_windows[4] = "Notes - Notepad"
    with pytest.raises(ToolRefused):
        run(box, "minimize_window", window="notepad")
    assert computer.log == []


def test_only_harmless_tools_run_without_confirmation(box):
    instant = {name for name in box.names if not box.get(name).needs_confirmation}
    assert instant == {
        "get_volume", "set_volume", "set_mute", "get_brightness", "set_brightness", "find_apps",
        "open_app", "list_windows", "focus_window", "minimize_window", "maximize_window", "restore_window",
        "remember", "recall", "notion_search", "notion_read", "gmail_search", "gmail_read",
        "undo_last_action", "open_website", "search_web", "read_window", "find_chat", "ask_grok",
        "watch_page", "list_watches", "stop_watching", "close_aplha", "share_view", "call_person", "start_professor_mode", "start_live_translation", "open_translated_reader", "cancel_power_off", "lock_computer",
        "sleep_computer", "play_media", "media_control", "get_time", "set_reminder", "list_reminders",
        "cancel_reminder", "get_weather", "get_news", "read_clipboard", "write_clipboard", "list_folder",
        "find_files", "read_file", "update_about_me",
    }


@pytest.mark.parametrize(
    "name", ["propose_close_window", "propose_type_text", "propose_press_keys", "propose_mouse_click"]
)
def test_risky_tools_can_only_propose(box, name):
    assert box.get(name).run is None
    assert box.get(name).needs_confirmation


def test_planning_touches_nothing(box, computer):
    plan(box, "propose_close_window", window="excel")
    plan(box, "propose_type_text", window="excel", text="hello")
    plan(box, "propose_press_keys", window="excel", keys="ctrl+s")
    plan(box, "propose_mouse_click", x=5, y=5)
    assert computer.log == []


def test_the_exact_text_is_shown_before_typing(box):
    assert plan(box, "propose_type_text", window="excel", text="send 100").preview == "send 100"


def test_typing_goes_to_the_named_window_only_after_focus(box, computer):
    message = plan(box, "propose_type_text", window="excel", text="hi").perform()
    assert computer.log == [("focus", 2), ("type", "hi")]
    assert "excel" in message.lower()


def test_nothing_is_typed_if_focus_fails(box, computer):
    computer.focus_works = False
    step = plan(box, "propose_type_text", window="excel", text="hi")
    with pytest.raises(ToolRefused):
        step.perform()
    assert ("type", "hi") not in computer.log


def test_a_window_that_stays_open_is_a_failure(box, computer):
    computer.close_works = False
    with pytest.raises(ToolRefused):
        plan(box, "propose_close_window", window="excel").perform()


@pytest.mark.parametrize("keys", ["ctrl+banana", "", "a+b+c+d+e"])
def test_unknown_keys_are_refused(box, keys):
    with pytest.raises(ToolRefused):
        plan(box, "propose_press_keys", window="excel", keys=keys)


def test_click_outside_the_screen_is_refused(box):
    with pytest.raises(ToolRefused):
        plan(box, "propose_mouse_click", x=5000, y=5)


class Rig:
    def __init__(self, computer):
        self.hub = AudioHub(lambda: FakeBackend())
        self.session = ScriptedSession()
        self.notices, self.proposals, self.resolved, self.states = [], [], [], []

        async def connect(key, model):
            return self.session

        self.conversation = Conversation(
            Settings(), self.hub, connector=connect, load_key=lambda: "k", toolbox=build_toolbox(computer),
            capture=self._capture,
        )
        c = self.conversation
        c.notice.connect(self.notices.append)
        c.proposal_ready.connect(self.proposals.append)
        c.proposal_resolved.connect(self.resolved.append)
        c.state_changed.connect(lambda state, detail: self.states.append(state))
        c.start()
        c.send_text("go")
        assert wait_until(lambda: self.session.sent)

    camera_works = True

    def _capture(self, source):
        if not self.camera_works:
            raise RuntimeError("no picture came")
        return b"jpeg"

    def ask(self, name, **arguments):
        self.session.give(self.conversation._loop, ToolRequest("c1", name, arguments))

    def tool_results(self):
        return [item for item in self.session.sent if item[0] == "tool"]

    def close(self):
        self.conversation.shutdown()
        self.hub.shutdown()


@pytest.fixture
def rig(qapp, computer):
    made = Rig(computer)
    yield made
    made.close()


def test_instant_tool_runs_shows_and_reports_the_real_result(rig, computer):
    rig.ask("set_volume", percent=20)
    assert wait_until(lambda: rig.tool_results())
    assert computer.percent == 20
    assert rig.tool_results()[0][3]["percent"] == 20
    assert wait_until(lambda: any("20%" in notice for notice in rig.notices))


def test_failed_tool_tells_the_model_the_truth(rig, computer):
    computer.stuck_volume = True
    rig.ask("set_volume", percent=20)
    assert wait_until(lambda: rig.tool_results())
    assert rig.tool_results()[0][3]["ok"] is False


def test_unexpected_crash_in_a_tool_is_contained(rig, computer):
    computer.volume = lambda: 1 / 0
    rig.ask("get_volume")
    assert wait_until(lambda: rig.tool_results())
    assert rig.tool_results()[0][3] == {"ok": False, "detail": "That failed (ZeroDivisionError)."}


def test_shutting_down_waits_and_a_model_cannot_add_approval(rig, computer):
    rig.ask("propose_power_off", action="shutdown", confirmed=True, approved=True)
    assert wait_until(lambda: rig.proposals)
    assert wait_until(lambda: rig.states[-1] is State.WARNING)
    assert computer.log == []
    assert rig.tool_results() == []


def test_person_confirming_carries_it_out_and_reports_it(rig, computer):
    rig.ask("propose_power_off", action="restart")
    assert wait_until(lambda: rig.proposals)
    rig.conversation.confirm(HumanPress(rig.proposals[0].id))
    assert wait_until(lambda: rig.tool_results())
    assert ("power", "restart") in computer.log
    assert rig.tool_results()[0][3]["completed"] is True


def test_cancel_does_nothing_and_tells_the_model(rig, computer):
    rig.ask("propose_power_off", action="shutdown")
    assert wait_until(lambda: rig.proposals)
    rig.conversation.cancel(rig.proposals[0].id)
    assert wait_until(lambda: rig.tool_results())
    assert computer.log == []
    assert rig.tool_results()[0][3]["completed"] is False


def test_shutting_down_always_waits_for_a_press(rig, computer):
    rig.ask("propose_power_off", action="shutdown")
    assert wait_until(lambda: rig.proposals)
    assert computer.log == []
    rig.conversation.confirm(HumanPress(rig.proposals[0].id))
    assert wait_until(lambda: ("power", "shutdown") in computer.log)


def test_only_shutdown_and_restart_are_accepted(box):
    with pytest.raises(ToolRefused):
        box.get("propose_power_off").propose({"action": "shutdown /s & del x"})


def test_lock_sleep_and_cancel_run_at_once(rig, computer):
    for name in ("lock_computer", "sleep_computer", "cancel_power_off"):
        rig.ask(name)
    assert wait_until(lambda: len(computer.log) == 3)
    assert computer.log == [("lock",), ("sleep",), ("power", "cancel")]


def test_asking_to_close_aplha_closes_it(rig, computer, monkeypatch):
    monkeypatch.setattr(conversation_module, "QUIT_DELAY_SECONDS", 0.0)
    closed = []
    rig.conversation.quit_requested.connect(lambda: closed.append(1))
    rig.ask("close_aplha")
    assert wait_until(lambda: closed)
    assert rig.proposals == []


def test_asking_gemini_for_a_class_opens_professor_mode(rig, computer):
    opened = []
    rig.conversation.class_requested.connect(lambda: opened.append(1))
    rig.ask("start_professor_mode")
    assert wait_until(lambda: opened)


def test_closing_a_window_runs_at_once(rig, computer):
    rig.ask("propose_close_window", window="excel")
    assert wait_until(lambda: rig.tool_results())
    assert rig.proposals == []


def test_only_shutting_down_and_restarting_ask(box):
    assert {name for name in box.names if box.get(name).always_asks} == {"propose_power_off"}


def test_what_the_owner_asks_for_runs_at_once_even_in_a_named_file(rig, computer):
    rig.ask("propose_type_text", window="excel", text="my password is here")
    assert wait_until(lambda: ("type", "my password is here") in computer.log)
    assert rig.proposals == []


def test_an_everyday_command_runs_without_asking(rig, computer):
    rig.ask("propose_type_text", window="untitled", text="hello")
    assert wait_until(lambda: rig.tool_results())
    assert ("type", "hello") in computer.log
    assert rig.tool_results()[0][3]["completed"] is True
    assert rig.proposals == []


def test_asking_to_be_seen_sends_the_picture_after_the_tool_result(rig, computer):
    rig.ask("share_view", source="camera", on=True)
    assert wait_until(lambda: any(item[0] == "image" for item in rig.session.sent))
    assert wait_until(lambda: rig.session.sent[-1][0] == "text")
    sent = [item[0] for item in rig.session.sent]
    assert sent.index("tool") < sent.index("image") < len(sent) - 1
    assert rig.conversation._vision == {"camera"}
    assert rig.tool_results()[0][3]["ok"] is True


def test_the_camera_can_be_turned_off_again(rig, computer):
    rig.ask("share_view", source="camera", on=True)
    assert wait_until(lambda: rig.tool_results())
    rig.ask("share_view", source="camera", on=False)
    assert wait_until(lambda: len(rig.tool_results()) == 2)
    assert rig.conversation._vision == set()


def test_a_camera_that_fails_is_reported_and_left_off(rig, computer):
    rig.camera_works = False
    rig.ask("share_view", source="camera", on=True)
    assert wait_until(lambda: rig.tool_results())
    assert rig.tool_results()[0][3]["ok"] is False
    assert rig.conversation._vision == set()


def test_only_the_camera_and_screen_can_be_shared(rig, computer):
    rig.ask("share_view", source="microphone", on=True)
    assert wait_until(lambda: rig.tool_results())
    assert rig.tool_results()[0][3]["ok"] is False


def video(computer, *controls):
    computer.open_windows[9] = "Song - YouTube - Google Chrome"
    computer.read_window = lambda handle: [
        {"name": name, "kind": kind, "x": 7, "y": 8} for name, kind in controls
    ]


@pytest.mark.parametrize("name", ["Skip", "Skip Ad", "Skip Ads", "skip ad ", "광고 건너뛰기", "건너뛰기"])
def test_the_skip_button_is_pressed_in_the_video_being_watched(computer, name):
    video(computer, ("Play", "button"), (name, "button"))
    computer.front = 9
    assert skip_ad(computer)
    assert ("click", 7, 8, False) in computer.log


@pytest.mark.parametrize(
    "name, kind",
    [("Skip navigation", "button"), ("Skip to content", "link"), ("Skip", "text"), ("Subscribe", "button")],
)
def test_other_things_on_the_page_are_left_alone(computer, name, kind):
    video(computer, (name, kind))
    computer.front = 9
    assert not skip_ad(computer)
    assert computer.log == []


def test_a_video_in_the_background_is_left_alone_so_focus_never_jumps(computer):
    video(computer, ("Skip", "button"))
    computer.front = 1
    assert not skip_ad(computer)
    assert computer.log == []


def test_a_mouse_click_the_owner_asked_for_runs_at_once(rig, computer):
    rig.ask("propose_mouse_click", x=5, y=5)
    assert wait_until(lambda: rig.tool_results())
    assert rig.proposals == []


def test_general_proposals_expire_too(computer):
    clock = [0.0]
    gate = ActionGate(clock=lambda: clock[0])
    step = build_toolbox(computer).get("propose_close_window").propose({"window": "excel"})
    proposal = gate.propose(step.summary, step.where, step.preview, step.perform)
    clock[0] += 61
    with pytest.raises(LookupError):
        gate.confirm(HumanPress(proposal.id))
    assert computer.log == []
