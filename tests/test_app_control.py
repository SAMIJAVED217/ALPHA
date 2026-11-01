"""Browser and app control against a pretend computer. Nothing here touches real apps."""

import pytest

from alpha.tools import computer as computer_module
from alpha.tools.catalogue import build_toolbox
from alpha.tools.registry import ToolRefused

from .test_computer_tools import FakeComputer


class Rig(FakeComputer):
    def __init__(self):
        super().__init__()
        self.open_windows[5] = "WhatsApp"
        self.opened = []
        self.video = "abcdefghijk"
        self.controls = [
            {"name": "Send", "kind": "button", "x": 10, "y": 20},
            {"name": "Search", "kind": "edit", "x": 30, "y": 40},
            {"name": "Sign in", "kind": "link", "x": 50, "y": 60},
        ]

    def open_url(self, url):
        self.opened.append(url)

    def first_video(self, search):
        return self.video

    def media_key(self, action):
        self.log.append(("media", action))

    def read_window(self, handle):
        return list(self.controls)


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    monkeypatch.setattr(computer_module, "STEP_SECONDS", 0.0)


@pytest.fixture
def pc():
    return Rig()


@pytest.fixture
def box(pc):
    return build_toolbox(pc)


def run(box, tool_name, /, **arguments):
    return box.get(tool_name).run(arguments)


def test_playing_opens_the_top_video_ready_to_start(box, pc):
    result = run(box, "play_media", what="lofi music")
    assert pc.opened == ["https://www.youtube.com/watch?v=abcdefghijk&autoplay=1"]
    assert "not confirmed" in result["detail"]


def test_with_no_video_found_it_shows_results_and_says_so(box, pc):
    pc.video = None
    result = run(box, "play_media", what="lofi music")
    assert "search_query=lofi+music" in pc.opened[0]
    assert "No video" in result["detail"]


def test_spotify_is_honest_that_it_needs_a_click(box, pc):
    result = run(box, "play_media", what="lofi & jazz", app="Spotify")
    assert pc.opened == ["spotify:search:lofi%20%26%20jazz"]
    assert "needs a click" in result["detail"]


def with_call_buttons(pc):
    pc.controls = [
        {"name": "Voice call", "kind": "button", "x": 1, "y": 2},
        {"name": "Video call", "kind": "button", "x": 3, "y": 4},
    ]


@pytest.mark.parametrize("kind, spot", [("voice", (1, 2)), ("video", (3, 4))])
def test_calling_opens_the_chat_and_presses_the_right_button(box, pc, kind, spot):
    with_call_buttons(pc)
    result = run(box, "call_person", app="WhatsApp", person="Ali", kind=kind)
    assert ("type", "Ali") in pc.log
    assert ("click", *spot, False) in pc.log
    assert result["ok"] is True


def test_with_no_call_button_it_says_so_and_clicks_nothing(box, pc):
    with pytest.raises(ToolRefused, match="call button"):
        run(box, "call_person", app="WhatsApp", person="Ali", kind="video")
    assert not any(step[0] == "click" for step in pc.log)


def test_only_voice_and_video_calls_exist(box, pc):
    with pytest.raises(ToolRefused, match="voice or video"):
        run(box, "call_person", app="WhatsApp", person="Ali", kind="fax")


def test_media_keys(box, pc):
    run(box, "media_control", action="next")
    assert ("media", "next") in pc.log


def test_unknown_media_action_is_refused(box):
    with pytest.raises(ToolRefused):
        run(box, "media_control", action="explode")


def plan(box, tool_name, /, **arguments):
    return box.get(tool_name).propose(arguments)


def test_website_gets_https_added(box, pc):
    run(box, "open_website", url="youtube.com")
    assert pc.opened == ["https://youtube.com"]


@pytest.mark.parametrize(
    "url",
    ["file:///C:/Windows/System32/cmd.exe", "javascript:alert(1)", "ms-settings:", "ftp://x.y", "C:\\Windows\\cmd.exe",
     "https://user:pass@evil.com", "https://a b.com", "whatsapp://send?text=x", "notahost", "data:text/html,x",
     "vbscript:x", "https://", "\\\\server\\share"],
)
def test_only_ordinary_web_addresses_open(box, pc, url):
    with pytest.raises(ToolRefused):
        run(box, "open_website", url=url)
    assert pc.opened == []


@pytest.mark.parametrize("url", ["example.com", "http://example.com/a?b=1", "example.com:8080/x", "localhost:3000"])
def test_normal_addresses_are_accepted(box, pc, url):
    run(box, "open_website", url=url)
    assert len(pc.opened) == 1


def test_search_text_is_escaped_into_the_address(box, pc):
    run(box, "search_web", query="cats & dogs?x=1")
    assert pc.opened == ["https://www.google.com/search?q=cats+%26+dogs%3Fx%3D1"]


def test_window_contents_are_marked_untrusted(box):
    result = run(box, "read_window", window="excel")
    assert {"label": "Send", "kind": "button"} in result["untrusted_window_contents"]


def test_clicking_a_label_only_proposes(box, pc):
    step = plan(box, "propose_click_control", window="excel", label="send")
    assert pc.log == []
    assert "Send" in step.summary
    step.perform()
    assert pc.log == [("focus", 2), ("click", 10, 20, False)]


def test_click_uses_where_the_control_is_now_not_where_it_was(box, pc):
    step = plan(box, "propose_click_control", window="excel", label="send")
    pc.controls[0] = {"name": "Send", "kind": "button", "x": 99, "y": 98}
    step.perform()
    assert ("click", 99, 98, False) in pc.log


def test_click_refuses_if_the_control_has_gone(box, pc):
    step = plan(box, "propose_click_control", window="excel", label="send")
    pc.controls.pop(0)
    with pytest.raises(ToolRefused):
        step.perform()
    assert not any(item[0] == "click" for item in pc.log)


def test_unknown_or_ambiguous_label_is_refused(box, pc):
    with pytest.raises(ToolRefused):
        plan(box, "propose_click_control", window="excel", label="delete")
    pc.controls.append({"name": "Send later", "kind": "button", "x": 1, "y": 1})
    pc.controls.append({"name": "Send now", "kind": "button", "x": 2, "y": 2})
    pc.controls.pop(0)
    with pytest.raises(ToolRefused):
        plan(box, "propose_click_control", window="excel", label="send")


def test_finding_a_chat_types_the_name_and_presses_no_enter(box, pc):
    result = run(box, "find_chat", app="whatsapp", person="Ali")
    assert pc.log == [("focus", 5), ("keys", ["ctrl", "f"]), ("type", "Ali")]
    assert "Nothing has been sent" in result["detail"]


def test_sending_needs_a_person_and_shows_the_exact_words(box, pc):
    step = plan(box, "propose_send_message", app="WhatsApp", person="Ali", text="I'm late")
    assert ("keys", ["enter"]) not in pc.log
    assert ("type", "I'm late") not in pc.log
    assert step.preview == "I'm late"
    assert "Ali" in step.summary and "WhatsApp" in step.summary


def test_send_opens_the_chat_then_types_then_presses_enter(box, pc):
    step = plan(box, "propose_send_message", app="WhatsApp", person="Ali", text="hello")
    assert pc.log == [("focus", 5), ("keys", ["ctrl", "f"]), ("type", "Ali")]
    pc.log.clear()
    step.perform()
    assert pc.log == [("focus", 5), ("keys", ["enter"]), ("type", "hello"), ("keys", ["enter"])]


def test_success_is_only_claimed_when_the_message_is_seen(box, pc):
    step = plan(box, "propose_send_message", app="WhatsApp", person="Ali", text="hello")
    assert "could not see" in step.perform()
    pc.controls.append({"name": "hello 11:30", "kind": "text", "x": 1, "y": 1})
    assert "now shows in the chat" in step.perform()


def test_nothing_is_typed_if_the_app_will_not_come_forward(box, pc):
    pc.focus_works = False
    with pytest.raises(ToolRefused):
        plan(box, "propose_send_message", app="WhatsApp", person="Ali", text="hello")
    assert not any(item[0] == "type" for item in pc.log)
    pc.focus_works = True
    step = plan(box, "propose_send_message", app="WhatsApp", person="Ali", text="hello")
    pc.focus_works = False
    with pytest.raises(ToolRefused):
        step.perform()
    assert ("type", "hello") not in pc.log


def test_app_that_is_closed_or_doubled_is_refused(box, pc):
    with pytest.raises(ToolRefused):
        run(box, "find_chat", app="telegram", person="Ali")
    pc.open_windows[6] = "WhatsApp"
    with pytest.raises(ToolRefused):
        run(box, "find_chat", app="whatsapp", person="Ali")


def test_the_real_app_is_chosen_over_a_browser_tab_about_it(box, pc):
    pc.open_windows[7] = "WhatsApp - Google Chrome"
    run(box, "find_chat", app="whatsapp", person="Ali")
    assert pc.log[0] == ("focus", 5)


def test_unknown_app_is_refused(box):
    with pytest.raises(ToolRefused):
        run(box, "find_chat", app="MySpace", person="Ali")


def test_sending_has_no_instant_route(box):
    assert box.get("propose_send_message").run is None
    assert box.get("propose_click_control").run is None
