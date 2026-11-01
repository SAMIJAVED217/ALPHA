import time
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QMainWindow

from alpha import icon, keystore, paths, remote, settings as settings_store, shortcut
from alpha.actions import ActionGate, EXPIRY_SECONDS, Outcome
from alpha.audio.hub import AudioHub
from alpha.audio.microphone import MicState
from alpha.settings import Settings
from alpha.state import DEFAULT_COLOURS, State
from alpha.ui.confirm_card import ConfirmButton, ConfirmCard
from alpha.ui.main_window import MainWindow
from alpha.ui.presence import ACTIVE_INTERVAL_MS, EASE_SECONDS, RESTING_INTERVAL_MS, Presence, PresenceModel
from alpha.ui.settings_dialog import SYSTEM_DEFAULT, SettingsDialog
from alpha.voice.conversation import Conversation

from .fakes import FakeBackend, ScriptedSession


def spin_until(condition, seconds=3.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if condition():
            return True
        time.sleep(0.005)
    return condition()


@pytest.fixture
def rig(qapp):
    backend = FakeBackend()
    hub = AudioHub(lambda: backend)
    settings = Settings()
    sessions = []

    async def connect(key, model):
        sessions.append(ScriptedSession())
        return sessions[-1]

    conversation = Conversation(settings, hub, connector=connect, load_key=keystore.load_key)
    window = MainWindow(settings, hub, conversation)
    conversation.start()
    window.show()
    window.activateWindow()
    QApplication.processEvents()

    class Rig:
        pass

    rig = Rig()
    rig.backend, rig.hub, rig.settings, rig.sessions = backend, hub, settings, sessions
    rig.conversation, rig.window = conversation, window
    yield rig
    QApplication.instance().removeEventFilter(window)
    window.close()
    conversation.shutdown()
    hub.shutdown()


# Presence.


def test_colour_eases_rather_than_jumps():
    model = PresenceModel()
    start = model.colour
    target = PresenceModel()
    target.set_state(State.THINKING)
    target.advance(EASE_SECONDS * 2)
    model.set_state(State.THINKING)
    assert model.colour == start
    model.advance(EASE_SECONDS / 2)
    assert model.colour != start and model.colour != target.colour
    assert model.easing
    model.advance(EASE_SECONDS)
    assert model.colour == target.colour
    assert not model.easing


@pytest.mark.parametrize("state", list(State))
def test_every_state_reaches_its_configured_colour(state):
    custom = dict(DEFAULT_COLOURS, **{state.value: "#102030"})
    model = PresenceModel(custom)
    model.set_state(State.ERROR if state is not State.ERROR else State.IDLE)
    model.advance(5)
    model.set_state(state)
    model.advance(5)
    assert model.colour == (0x10, 0x20, 0x30)


def test_default_colours_match_their_names():
    from PySide6.QtGui import QColor

    hue = {name: QColor(value).hue() for name, value in DEFAULT_COLOURS.items()}
    assert 200 <= hue["idle"] <= 240
    assert 170 <= hue["listening"] <= 190
    assert 250 <= hue["thinking"] <= 280
    assert 190 <= hue["speaking"] <= 215
    assert 30 <= hue["warning"] <= 45
    assert hue["error"] <= 10 or hue["error"] >= 345


def test_audio_level_moves_the_filaments():
    quiet, loud = PresenceModel(), PresenceModel()
    loud.set_level(1.0)
    for _ in range(30):
        quiet.advance(0.03)
        loud.advance(0.03)
    assert loud.level > 0.9
    assert loud.filament_lengths().mean() > quiet.filament_lengths().mean() * 1.5
    assert loud.level > quiet.level


def test_level_is_kept_in_range():
    model = PresenceModel()
    model.set_level(9.0)
    model.advance(5)
    assert model.level <= 1.0


def test_rendering_stops_when_hidden_or_minimised(qapp):
    window = QMainWindow()
    presence = Presence()
    window.setCentralWidget(presence)
    window.show()
    assert spin_until(lambda: presence.animating)
    assert spin_until(lambda: presence.frames_drawn > 1)

    window.hide()
    assert spin_until(lambda: not presence.animating)
    drawn = presence.frames_drawn
    QTest.qWait(150)
    assert presence.frames_drawn == drawn

    window.show()
    assert spin_until(lambda: presence.animating)
    window.showMinimized()
    assert spin_until(lambda: not presence.animating)
    window.showNormal()
    assert spin_until(lambda: presence.animating)
    window.close()


def test_falls_back_to_processor_drawing_without_a_graphics_chip(qapp):
    from alpha.ui.presence_gl import graphics_chip_ready, make_presence

    assert graphics_chip_ready() is False
    assert type(make_presence()) is Presence


def test_frame_rate_drops_at_rest(qapp):
    presence = Presence()
    presence.show()
    assert spin_until(lambda: presence.animating)
    assert spin_until(lambda: presence._timer.interval() == RESTING_INTERVAL_MS)
    presence.set_state(State.THINKING)
    assert presence._timer.interval() == ACTIVE_INTERVAL_MS
    presence.close()


# Main window.


def test_phone_control_is_off_at_start_and_always_shows_when_on(rig, monkeypatch):
    monkeypatch.setattr(rig.window._remote, "_port", 0)
    monkeypatch.setattr(rig.window._remote, "_host", "127.0.0.1")
    monkeypatch.setattr(remote, "addresses", lambda: ["192.168.1.20"])
    assert not rig.window._remote.running and rig.window.remote_indicator.isHidden()
    rig.window.remote_action.setChecked(True)
    assert not rig.window.remote_indicator.isHidden()
    assert "PHONE CONTROL ON" in rig.window.remote_indicator.text()
    assert "phone control" in rig.window._live_inputs()
    assert rig.window._remote.control._code in rig.window._remote_dialog.details.text()
    assert not rig.window._remote_dialog.picture.pixmap().isNull()
    rig.window._remote_dialog.close()
    rig.window.remote_action.setChecked(False)
    assert not rig.window._remote.running and rig.window.remote_indicator.isHidden()


def test_a_message_from_the_phone_shows_and_goes_to_the_conversation(rig, monkeypatch):
    sent = []
    monkeypatch.setattr(rig.conversation, "send_text", sent.append)
    rig.window._phone_said("hello")
    assert sent == ["hello"]
    assert spin_until(lambda: "(from phone) hello" in rig.window.transcript.toPlainText())


def test_window_shows_every_state_it_is_told(rig):
    for state in State:
        rig.window.show_state(state, "detail text")
        assert rig.window.state_label.text() == state.label.upper()
        assert rig.window.detail_label.text() == "detail text"
        assert rig.window.presence.model.state is state


def test_mic_indicator_shows_each_mic_state(rig):
    assert rig.window.mic_indicator.text() == "MIC OFF"
    rig.window.show_mic(MicState.OPEN)
    assert "MIC OPEN" in rig.window.mic_indicator.text()
    assert rig.window.mic_indicator.objectName() == "micOpen"
    rig.window.show_mic(MicState.CLOSED)
    assert rig.window.mic_indicator.text() == "MIC OFF"


def key_event(kind, key, repeat=False):
    return QKeyEvent(kind, key, Qt.KeyboardModifier.NoModifier, "", repeat)


def press_talk_key(rig, kind, repeat=False):
    event = key_event(kind, Qt.Key.Key_F9, repeat)
    return rig.window.eventFilter(rig.window, event)


@pytest.fixture
def with_key(rig):
    keystore.save_key("a-key")
    rig.conversation.settings_changed()
    assert spin_until(lambda: rig.conversation._key_present)
    return rig


def test_talking_opens_the_mic_only_while_held(with_key, monkeypatch):
    rig = with_key
    assert rig.backend.opened_inputs == []
    rig.window._talk_down()
    assert spin_until(lambda: "MIC OPEN" in rig.window.mic_indicator.text())
    assert rig.backend.mic_is_open
    rig.window._talk_up()
    assert spin_until(lambda: rig.window.mic_indicator.text() == "MIC OFF")
    assert not rig.backend.mic_is_open


def test_key_opens_the_mic_only_while_held_and_ignores_repeats(with_key, monkeypatch):
    rig = with_key
    monkeypatch.setattr(rig.window, "_window_in_charge", lambda: True)
    assert press_talk_key(rig, QEvent.Type.KeyPress)
    assert spin_until(lambda: rig.backend.mic_is_open)
    assert press_talk_key(rig, QEvent.Type.KeyPress, repeat=True) is False
    assert press_talk_key(rig, QEvent.Type.KeyRelease)
    assert spin_until(lambda: rig.window.mic_indicator.text() == "MIC OFF")
    assert not rig.backend.mic_is_open


def test_losing_focus_closes_the_mic(with_key, monkeypatch):
    rig = with_key
    monkeypatch.setattr(rig.window, "_window_in_charge", lambda: True)
    press_talk_key(rig, QEvent.Type.KeyPress)
    assert spin_until(lambda: rig.backend.mic_is_open)
    rig.window.eventFilter(rig.window, QEvent(QEvent.Type.WindowDeactivate))
    assert spin_until(lambda: not rig.backend.mic_is_open)


def test_talk_key_is_ignored_while_another_window_is_active(with_key, monkeypatch):
    rig = with_key
    monkeypatch.setattr(rig.window, "_window_in_charge", lambda: False)
    assert press_talk_key(rig, QEvent.Type.KeyPress) is False
    assert rig.backend.opened_inputs == []


def test_typed_message_is_sent_and_shown(with_key):
    rig = with_key
    rig.window.input.setText("hello there")
    QTest.keyClick(rig.window.input, Qt.Key.Key_Return)
    assert spin_until(lambda: rig.sessions and rig.sessions[0].sent == [("text", "hello there")])
    assert "You: hello there" in rig.window.transcript.toPlainText()
    assert rig.window.input.text() == ""


def test_streamed_reply_joins_up_in_the_transcript(rig):
    rig.window._append_reply("Hello ")
    rig.window._append_reply("world")
    rig.window._reply_finished()
    rig.window._append_reply("Next")
    assert rig.window.transcript.toPlainText().splitlines() == ["APLHA: Hello world", "APLHA: Next"]


def test_model_text_is_shown_as_plain_text(rig):
    rig.window._append_reply("<img src=x>")
    assert "<img src=x>" in rig.window.transcript.toPlainText()


# Confirm card, in the real window.


def propose(rig, name="note.txt", content="hello"):
    rig.window.input.setText(f"/file {name} | {content}")
    rig.window.send()
    assert spin_until(lambda: rig.window.card.isVisible())
    return rig.conversation.gate.current


def arm(rig):
    assert spin_until(lambda: rig.window.card.confirm_button.isEnabled(), 2)


def test_file_command_shows_a_card_and_creates_nothing_yet(rig):
    proposal = propose(rig)
    assert proposal.file_name == "note.txt"
    assert "note.txt" in rig.window.card._title.text()
    assert str(proposal.target) in rig.window.card._where.text()
    assert not proposal.target.exists()


def test_clicking_confirm_creates_the_file(rig):
    proposal = propose(rig)
    arm(rig)
    QTest.mouseClick(rig.window.card.confirm_button, Qt.MouseButton.LeftButton)
    assert spin_until(lambda: not rig.window.card.isVisible())
    assert proposal.target.read_text() == "hello"
    assert "read it back" in rig.window.transcript.toPlainText()


def test_confirm_cannot_be_pressed_until_armed(rig):
    proposal = propose(rig)
    assert not rig.window.card.confirm_button.isEnabled()
    QTest.mouseClick(rig.window.card.confirm_button, Qt.MouseButton.LeftButton)
    QTest.qWait(50)
    assert not proposal.target.exists()
    assert rig.conversation.gate.current is proposal


def test_programmatic_click_cannot_confirm(rig):
    proposal = propose(rig)
    arm(rig)
    rig.window.card.confirm_button.click()
    rig.window.card.confirm_button.animateClick()
    QTest.qWait(200)
    assert not proposal.target.exists()
    assert rig.conversation.gate.current is proposal


def test_non_spontaneous_mouse_event_cannot_confirm(rig):
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QMouseEvent

    proposal = propose(rig)
    arm(rig)
    button = rig.window.card.confirm_button
    point = QPointF(5, 5)
    for kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
        event = QMouseEvent(
            kind, point, button.mapToGlobal(point), Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(button, event)
    QTest.qWait(50)
    assert not proposal.target.exists()


def test_keyboard_cannot_reach_confirm(rig):
    propose(rig)
    arm(rig)
    assert rig.window.card.confirm_button.focusPolicy() == Qt.FocusPolicy.NoFocus
    assert rig.window.card.cancel_button.focusPolicy() == Qt.FocusPolicy.NoFocus


def test_cancel_button_creates_nothing(rig):
    proposal = propose(rig)
    QTest.mouseClick(rig.window.card.cancel_button, Qt.MouseButton.LeftButton)
    assert spin_until(lambda: not rig.window.card.isVisible())
    assert proposal.outcome is Outcome.CANCELLED
    assert not proposal.target.exists()


def test_card_counts_down_and_vanishes_on_expiry(qapp):
    backend = FakeBackend()
    hub = AudioHub(lambda: backend)
    gate = ActionGate(expiry_seconds=0.4)
    conversation = Conversation(Settings(), hub, gate=gate, load_key=lambda: "")
    window = MainWindow(Settings(), hub, conversation)
    conversation.start()
    window.show()
    try:
        conversation.propose_file("late.txt", "x")
        assert spin_until(lambda: window.card.isVisible())
        assert "Expires in" in window.card._countdown.text()
        proposal = gate.current
        assert spin_until(lambda: not window.card.isVisible())
        assert proposal.outcome is Outcome.EXPIRED
        assert not proposal.target.exists()
    finally:
        QApplication.instance().removeEventFilter(window)
        window.close()
        conversation.shutdown()
        hub.shutdown()


def test_default_expiry_is_short_but_readable():
    assert 30 <= EXPIRY_SECONDS <= 120


def test_bad_file_command_explains_itself(rig):
    rig.window.input.setText("/file oops")
    rig.window.send()
    assert "/file notes.txt" in rig.window.transcript.toPlainText()
    assert not rig.window.card.isVisible()


# Settings and first-run.


def make_dialog(rig, first_run=False):
    return SettingsDialog(rig.settings, rig.hub, rig.conversation, first_run, rig.window)


def test_first_run_says_so_and_asks_for_the_key(rig):
    dialog = make_dialog(rig, first_run=True)
    assert dialog.windowTitle() == "Welcome to APLHA"
    assert dialog.key_field.echoMode() == dialog.key_field.EchoMode.Password
    assert "Paste" in dialog.key_field.placeholderText()


def test_devices_are_listed_without_blocking(rig):
    dialog = make_dialog(rig)
    assert dialog.microphone_box.itemText(0) == SYSTEM_DEFAULT
    assert spin_until(lambda: dialog.microphone_box.findData("Test mic") >= 0)
    assert dialog.speakers_box.findData("Test speakers") >= 0
    assert "1 microphones" in dialog.device_status.text()


def test_unplugged_device_is_kept_and_marked(rig):
    rig.settings.microphone = "Old mic"
    dialog = make_dialog(rig)
    assert spin_until(lambda: dialog.microphone_box.findData("Test mic") >= 0)
    assert dialog.microphone_box.currentData() == "Old mic"
    assert "not connected" in dialog.microphone_box.currentText()


def test_saving_stores_the_key_in_the_credential_store_and_the_rest_in_settings(rig):
    dialog = make_dialog(rig, first_run=True)
    assert spin_until(lambda: dialog.speakers_box.findData("Test speakers") >= 0)
    dialog.key_field.setText("  my-secret-key ")
    dialog.microphone_box.setCurrentIndex(dialog.microphone_box.findData("Test mic"))
    dialog.speakers_box.setCurrentIndex(dialog.speakers_box.findData("Test speakers"))
    dialog.talk_key_box.setCurrentText("F8")
    dialog.colours["idle"] = "#010203"
    dialog._save()
    assert dialog.result() == QDialog.DialogCode.Accepted
    rig.window.apply_settings(dialog.result_settings())

    assert keystore.load_key() == "my-secret-key"
    text = paths.settings_file().read_text(encoding="utf-8")
    assert "my-secret-key" not in text
    saved = settings_store.load()
    assert saved.setup_complete is True
    assert saved.microphone == "Test mic" and saved.speakers == "Test speakers"
    assert saved.talk_key == "F8"
    assert saved.colours["idle"] == "#010203"
    assert "hold F8 to talk" in rig.window.input.placeholderText()
    assert spin_until(lambda: rig.window.presence.model._to == (1, 2, 3))
    assert spin_until(lambda: rig.conversation._key_present)


def test_leaving_the_key_blank_keeps_the_saved_one(rig):
    keystore.save_key("existing")
    dialog = make_dialog(rig)
    assert "saved" in dialog.key_field.placeholderText()
    dialog._save()
    assert keystore.load_key() == "existing"


def test_check_key_shows_the_real_answer(rig):
    dialog = make_dialog(rig)
    dialog.key_field.setText("abc")
    dialog._check_key()
    assert spin_until(lambda: "accepted" in dialog.key_status.text())


def test_test_button_plays_on_the_selected_speakers(rig):
    dialog = make_dialog(rig)
    assert spin_until(lambda: dialog.speakers_box.findData("Test speakers") >= 0)
    dialog.speakers_box.setCurrentIndex(dialog.speakers_box.findData("Test speakers"))
    dialog.test_button.click()
    assert spin_until(lambda: rig.backend.opened_outputs)
    assert rig.backend.opened_outputs[0][0] == "Test speakers"


def test_reset_restores_default_colours(rig):
    dialog = make_dialog(rig)
    dialog.colours["idle"] = "#000000"
    dialog._reset_colours()
    assert dialog.colours == DEFAULT_COLOURS


def test_saved_settings_reach_the_next_start(rig):
    rig.window.apply_settings(Settings(setup_complete=True, speakers="Headphones"))
    assert settings_store.load().speakers == "Headphones"


# Packaging.


def test_icon_file_is_original_and_valid():
    path = paths.assets_dir() / "alpha.ico"
    data = path.read_bytes()
    assert data[:4] == b"\x00\x00\x01\x00"
    assert int.from_bytes(data[4:6], "little") == len(icon.ICON_SIZES)


def test_icon_can_be_rebuilt_and_loads(qapp, tmp_path):
    from PySide6.QtGui import QIcon

    target = tmp_path / "rebuilt.ico"
    icon.write_ico(target)
    assert not QIcon(str(target)).isNull()
    assert not QIcon(str(target)).pixmap(32, 32).isNull()
    image = icon.draw(64)
    assert image.pixelColor(32, 32).lightness() > 200
    assert image.pixelColor(0, 0).alpha() == 0


def test_shortcut_passes_paths_outside_the_command_text():
    script = shortcut._SCRIPT
    assert "$env:ALPHA_LINK_TARGET" in script
    assert str(Path.home()) not in script
    assert shortcut.windowless_python().name == "pythonw.exe"
