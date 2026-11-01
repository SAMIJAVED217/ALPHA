from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from alpha import keystore
from alpha.audio.hub import AudioHub
from alpha.audio.microphone import MicState
from alpha.memory import MemoryStore
from alpha.settings import Settings
from alpha.state import State
from alpha.telemetry import Reading, Telemetry, speed_text
from alpha.tools.catalogue import build_toolbox
from alpha.tools.registry import ToolBox, ToolRefused, UndoStack
from alpha.tools.memory_tools import memory_tools
from alpha.ui.hud_panel import UNKNOWN, HudPanel
from alpha.ui.main_window import COMPACT_SIZE, MainWindow
from alpha.voice.conversation import Conversation
from alpha.voice.events import ReplyAudio
from alpha.voice.hands_free import Mode

from .fakes import FakeBackend, ScriptedSession
from .test_audio import tone
from .test_computer_tools import FakeComputer
from alpha import settings as settings_store

from .test_conversation import Rig as ConversationRig
from .test_interface import spin_until


@pytest.fixture
def hud(qapp):
    hub = AudioHub(lambda: FakeBackend())
    sessions = []

    async def connect(key, model):
        sessions.append(ScriptedSession())
        return sessions[-1]

    conversation = Conversation(
        Settings(), hub, connector=connect, load_key=lambda: "k", toolbox=build_toolbox(FakeComputer())
    )
    window = MainWindow(Settings(), hub, conversation)
    conversation.start()
    window.show()
    QApplication.processEvents()
    yield window, conversation
    QApplication.instance().removeEventFilter(window)
    window._ticker.stop()
    window.close()
    conversation.shutdown()
    hub.shutdown()


def test_no_face_code_remains():
    root = Path(__file__).parent.parent / "alpha"
    assert not (root / "face_build.py").exists()
    for source in root.rglob("*.py"):
        assert "hologram" not in source.read_text(encoding="utf-8").lower()


def test_old_settings_with_a_face_choice_still_load(tmp_path):
    from alpha import settings

    path = tmp_path / "settings.json"
    path.write_text('{"setup_complete": true, "show_face": true}', encoding="utf-8")
    assert settings.load(path).setup_complete is True


def test_telemetry_gives_real_sensible_numbers():
    reading = Telemetry().read()
    assert 0 <= reading.cpu <= 100
    assert 0 < reading.memory <= 100
    assert 0 < reading.disk <= 100
    assert reading.down_kb >= 0 and reading.up_kb >= 0


def test_speed_text():
    assert speed_text(12) == "12 KB/s"
    assert speed_text(2048) == "2.0 MB/s"


def test_panel_shows_a_dash_until_it_has_a_real_value(qapp):
    panel = HudPanel("X")
    panel.add_row("CPU", with_meter=True)
    assert panel.value("CPU") == UNKNOWN
    panel.set_value("CPU", "40%", 40)
    assert panel.value("CPU") == "40%"
    assert panel._meters["CPU"].value == 40


def test_meter_stays_in_range(qapp):
    panel = HudPanel("X")
    panel.add_row("CPU", with_meter=True)
    panel.set_value("CPU", "x", 900)
    assert panel._meters["CPU"].value == 100
    panel.set_value("CPU", UNKNOWN, None)
    assert panel._meters["CPU"].value == 0


def test_panels_show_readings_and_a_missing_battery_stays_a_dash(hud):
    window, _ = hud
    window._telemetry.read = lambda: Reading(12, 34, 56, None, False, 1, 2)
    window.refresh_panels()
    assert window.gauges["CPU"].percent == 12
    assert window.system_panel.value("BATTERY") == UNKNOWN
    assert window.system_panel.value("DOWN") == "1 KB/s"


def test_panels_do_not_measure_while_hidden(hud):
    window, _ = hud
    calls = []
    window._telemetry.read = lambda: calls.append(1)
    window.hide()
    window.refresh_panels()
    assert calls == []


def test_link_panel_tells_the_truth_about_inputs(hud):
    window, _ = hud
    assert window.link_panel.value("GEMINI") == "STANDBY"
    window.show_mic(MicState.OPEN)
    assert window.link_panel.value("MIC") == "ON"
    window.show_sharing({"screen"})
    assert window.link_panel.value("SCREEN") == "SHARED"
    assert window.link_panel.value("CAMERA") == "OFF"
    window.show_mic(MicState.CLOSED)
    assert window.link_panel.value("MIC") == "OFF"


def test_inputs_read_off_from_the_start(hud):
    window, _ = hud
    for name in ("MIC", "HANDS-FREE", "SCREEN", "CAMERA"):
        assert window.link_panel.value(name) == "OFF"


def test_mic_pill_says_exactly_where_your_voice_is_going(hud):
    window, _ = hud
    window.show_mic(MicState.OPEN)
    assert "SENDING TO GEMINI" in window.mic_indicator.text()
    window.settings.speech_engine = "offline"
    window.show_mic(MicState.OPEN)
    assert "STAYS ON THIS PC" in window.mic_indicator.text()
    window.show_hands_free(Mode.TURN)
    assert "STAYS ON THIS PC" in window.mic_indicator.text()
    window.settings.speech_engine = "gemini"
    window.show_mic(MicState.OPEN)
    assert "SENDING TO GEMINI" in window.mic_indicator.text()


def test_gemini_link_goes_live_only_after_a_real_connection(hud):
    window, conversation = hud
    conversation.send_text("hello")
    assert spin_until(lambda: window.link_panel.value("GEMINI") == "LIVE")


def test_panels_take_the_state_colour(hud):
    window, _ = hud
    window.show_state(State.ERROR, "")
    assert window.system_panel._tint.name() == window.settings.colours["error"]


def test_compact_hides_the_log_but_never_the_indicators_and_can_return(hud):
    window, _ = hud
    window.show_sharing({"screen"})
    window.show_mic(MicState.OPEN)
    window.set_compact(True)
    assert not window.wordmark.isVisible()
    assert window.mic_alert.isVisible() and window.sharing_indicator.isVisible()
    assert window.right_drawer.arrow.isVisible()
    assert window.height() == COMPACT_SIZE.height()
    assert window.width() < 980
    window.set_compact(False)
    assert window.wordmark.isVisible()


def test_fullscreen_can_be_left_and_keeps_its_menu_reachable(hud):
    window, _ = hud
    window.show()
    window.fullscreen_action.setChecked(True)
    assert window.isFullScreen()
    window.fullscreen_action.setChecked(False)
    assert not window.isFullScreen()


def test_esc_leaves_fullscreen_when_there_is_no_reply_to_stop(hud):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent

    window, _ = hud
    window.show()
    window._window_in_charge = lambda: True
    window.fullscreen_action.setChecked(True)
    press = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(window, press)
    assert not window.fullscreen_action.isChecked()


def test_the_top_bar_shows_true_readings_and_a_dash_when_unknown(hud):
    window, _ = hud
    window._telemetry.read = lambda: Reading(12, 34, 56, None, False, 1, 2)
    window.refresh_panels()
    assert window.readings.text() == "CPU 12%   RAM 34%"
    window._telemetry.read = lambda: Reading(None, None, 56, None, False, 1, 2)
    window.refresh_panels()
    assert "12%" not in window.readings.text()


def test_turning_hands_free_off_is_remembered(hud):
    window, _ = hud
    window._hands_free_pressed(False)
    assert settings_store.load().hands_free_at_start is False


def test_always_on_top_can_be_toggled(hud):
    from PySide6.QtCore import Qt

    window, _ = hud
    window.set_always_on_top(True)
    assert window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    window.set_always_on_top(False)
    assert not window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint


def test_live_inputs_are_named_for_the_tray(hud):
    window, _ = hud
    assert window._live_inputs() == ""
    window.show_mic(MicState.OPEN)
    assert "microphone" in window._live_inputs()
    window.show_mic(MicState.CLOSED)
    window.show_sharing({"camera"})
    assert "sharing" in window._live_inputs()


def test_undo_stack_is_newest_first_and_bounded():
    stack = UndoStack()
    order = []
    for number in range(30):
        stack.push(str(number), lambda n=number: order.append(n))
    stack.undo()
    stack.undo()
    assert order == [29, 28]
    assert len(stack._steps) == UndoStack.LONGEST - 2


def test_nothing_to_undo_is_said_plainly():
    with pytest.raises(ToolRefused):
        UndoStack().undo()


@pytest.fixture
def computer():
    return FakeComputer()


def test_undo_restores_the_volume_it_found(computer):
    box = build_toolbox(computer)
    box.get("set_volume").run({"percent": 80})
    assert computer.percent == 80
    assert "40%" in box.get("undo_last_action").run({})["detail"]
    assert computer.percent == 40


def test_undo_restores_brightness_mute_and_windows(computer):
    box = build_toolbox(computer)
    box.get("set_brightness").run({"percent": 10})
    box.get("set_mute").run({"muted": True})
    box.get("minimize_window").run({"window": "excel"})
    box.undo.undo()
    assert ("restore", 2) in computer.log
    box.undo.undo()
    assert computer.muted is False
    box.undo.undo()
    assert computer.bright == 70


def test_a_failed_change_leaves_nothing_to_undo(computer):
    computer.stuck_volume = True
    box = build_toolbox(computer)
    with pytest.raises(ToolRefused):
        box.get("set_volume").run({"percent": 80})
    with pytest.raises(ToolRefused):
        box.undo.undo()


def test_undo_removes_a_new_memory(tmp_path):
    store = MemoryStore(tmp_path / "m.sqlite")
    undo = UndoStack()
    box = ToolBox(memory_tools(store, undo), undo)
    box.get("remember").run({"text": "likes tea"})
    assert store.count() == 1
    box.undo.undo()
    assert store.count() == 0
    store.close()


def test_undo_button_reports_in_the_transcript(hud):
    window, _ = hud
    window.undo_button.click()
    assert spin_until(lambda: "nothing to undo" in window.transcript.toPlainText())


def test_a_reply_arriving_during_playback_still_shows_speaking(qapp):
    rig = ConversationRig()
    try:
        rig.conversation.send_text("one")
        assert spin_until(lambda: rig.sessions and rig.session.sent)
        rig.give(ReplyAudio(tone(24_000, 2.0)))
        assert spin_until(lambda: rig.states[-1] is State.SPEAKING)
        rig.conversation.send_text("two")
        assert spin_until(lambda: rig.states[-1] is State.THINKING)
        rig.give(ReplyAudio(tone(24_000, 0.5)))
        assert spin_until(lambda: rig.states[-1] is State.SPEAKING)
    finally:
        rig.close()


def test_key_helper_is_still_available():
    assert callable(keystore.has_key)
