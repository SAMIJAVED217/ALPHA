from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from alpha.attachments import Attachment
from alpha.audio.microphone import MicState
from alpha.voice.hands_free import Mode

from .test_hud import hud  # noqa: F401
from .test_interface import spin_until


def arranged(window):
    window.show()
    QApplication.processEvents()
    window._arrange()
    return window


def test_only_the_character_and_its_status_show_at_first(hud):  # noqa: F811
    window = arranged(hud[0])
    assert window.state_label.isVisible() and window.message_button.isVisible()
    assert window.clock.isVisible() and window.readings.isVisible()
    for hidden in (window.system_panel, window.files_panel, window.link_panel, window.character_switch,
                   window.settings_button, window.transcript, window.input,
                   window.mic_indicator, window.mic_alert):
        assert not hidden.isVisible()
    assert window.left_drawer.arrow.isVisible() and window.right_drawer.arrow.isVisible()


def test_arrows_pull_each_side_out_and_back(hud):  # noqa: F811
    window = arranged(hud[0])
    window.resize(1400, 800)
    QApplication.processEvents()
    window._arrange()
    window.left_drawer.arrow.click()
    assert window.transcript.isVisible() and window.mic_indicator.isVisible()
    assert not window.link_panel.isVisible()
    assert spin_until(lambda: window.left_drawer.x() == 16)
    window.right_drawer.arrow.click()
    assert window.character_switch.isVisible() and window.system_panel.isVisible() and window.clock.isVisible()
    assert spin_until(lambda: window.right_drawer.geometry().right() == window.stage_hole.geometry().right())
    window.left_drawer.arrow.click()
    assert spin_until(lambda: not window.transcript.isVisible())


def test_arrows_point_the_way_the_drawer_will_move(hud):  # noqa: F811
    window = arranged(hud[0])
    assert window.left_drawer.arrow.text() == "\u276f"
    assert window.right_drawer.arrow.text() == "\u276e"
    window.left_drawer.arrow.click()
    assert window.left_drawer.arrow.text() == "\u276e"


def test_the_3d_buttons_open_the_side_panels(hud):  # noqa: F811
    window = arranged(hud[0])
    window.message_button.click()
    assert window.left_drawer.is_open
    window.state_label.click()
    assert window.right_drawer.is_open


def test_a_3d_button_leans_towards_the_pointer_and_settles(hud):  # noqa: F811
    window = arranged(hud[0])
    button = window.message_button
    spot = QPointF(button.width() - 2, 20)
    moved = QMouseEvent(QEvent.Type.MouseMove, spot, button.mapToGlobal(spot), Qt.MouseButton.NoButton,
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(button, moved)
    assert button.tilt > 0.8
    QApplication.sendEvent(button, QEvent(QEvent.Type.Leave))
    assert button.tilt == 0.0


def test_a_3d_button_draws_in_every_pose(hud):  # noqa: F811
    window = arranged(hud[0])
    button = window.state_label
    button.tilt = -1.0
    button.setDown(True)
    assert not button.grab().isNull()
    button.setDown(False)


def test_the_arrows_sit_level(hud):  # noqa: F811
    window = arranged(hud[0])
    assert window.left_drawer.arrow.y() == window.right_drawer.arrow.y()


def test_the_character_stays_centred_with_drawers_open(hud):  # noqa: F811
    window = arranged(hud[0])
    middle = window.presence.focus_x
    window.right_drawer.set_open(True)
    window._arrange()
    assert window.presence.focus_x == middle


def test_an_open_mic_shows_on_the_main_screen_with_the_drawers_shut(hud):  # noqa: F811
    window = arranged(hud[0])
    window.show_mic(MicState.OPEN)
    assert window.mic_alert.isVisible() and "MIC OPEN" in window.mic_alert.text()
    assert window.mic_alert.objectName() == "micOpen"
    window.show_mic(MicState.CLOSED)
    assert not window.mic_alert.isVisible() and window.mic_indicator.text() == "MIC OFF"


def test_local_listening_is_a_tiny_dot_with_the_words_in_the_drawer(hud):  # noqa: F811
    window = arranged(hud[0])
    window.show_hands_free(Mode.WAITING)
    window.show_mic(MicState.OPEN)
    assert "STAYS ON THIS PC" in window.mic_indicator.text()
    assert window.mic_alert.text() == "\u25cf" and window.mic_alert.objectName() == "micDot"
    assert window.mic_alert.isVisible() and "STAYS ON THIS PC" in window.mic_alert.toolTip()
    window.show_hands_free(Mode.TURN)
    window.show_mic(MicState.OPEN)
    assert "SENDING TO GEMINI" in window.mic_alert.text() and window.mic_alert.objectName() == "micOpen"


def test_no_drawer_can_cover_a_live_indicator(hud):  # noqa: F811
    window = arranged(hud[0])
    window.resize(980, 720)
    QApplication.processEvents()
    window._arrange()
    window.show_mic(MicState.OPEN)
    window.show_sharing({"screen"})
    QApplication.processEvents()
    window.left_drawer.set_open(True)
    assert spin_until(lambda: window.left_drawer.x() == 16)
    for label in (window.mic_alert, window.sharing_indicator):
        assert window.presence.childAt(label.geometry().center()) is label


def test_on_a_small_window_one_drawer_opens_at_a_time(hud):  # noqa: F811
    window = arranged(hud[0])
    window.resize(760, 520)
    QApplication.processEvents()
    window.left_drawer.set_open(True)
    window.right_drawer.set_open(True)
    assert not window.left_drawer.is_open and window.right_drawer.is_open


def test_the_talk_key_works_with_the_drawers_shut(hud):  # noqa: F811
    window, conversation = hud
    arranged(window)
    window._talk_down()
    assert window._talking
    window._talk_up()
    assert not window._talking


def test_sharing_shows_even_with_the_drawers_shut(hud):  # noqa: F811
    window = arranged(hud[0])
    assert not window.sharing_indicator.isVisible()
    window.show_sharing({"camera", "screen"})
    assert window.sharing_indicator.isVisible()
    assert window.sharing_indicator.text() == "\u25cf CAMERA + SCREEN SHARED"
    window.show_sharing(set())
    assert not window.sharing_indicator.isVisible()


def test_a_confirm_card_shuts_the_drawers(hud):  # noqa: F811
    window, conversation = hud
    arranged(window)
    window.resize(1400, 800)
    QApplication.processEvents()
    window.right_drawer.set_open(True)
    window.left_drawer.set_open(True)
    conversation.propose_file("note.txt", "hello")
    assert spin_until(lambda: window.card.isVisible())
    assert spin_until(lambda: not window.right_drawer.isVisible() and not window.left_drawer.isVisible())


def test_adding_a_file_shows_it(hud):  # noqa: F811
    window, conversation = hud
    arranged(window)
    conversation.add_attachment(Attachment("note.txt", text="hello"))
    assert spin_until(lambda: window.files_panel.isVisible())
