"""ALPHA's main window."""

from __future__ import annotations

import logging
import subprocess
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QEvent, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QColor, QIcon, QImage, QKeySequence, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from .. import attachments, autostart, characters, languages, paths, remote, settings as settings_store
from ..audio.hub import AudioHub
from ..audio.microphone import MicState
from ..settings import SPEECH_LANGUAGES, Settings
from ..state import State
from ..telemetry import Telemetry, speed_text
from ..tools.catalogue import declarations
from ..voice.conversation import Conversation
from ..voice.hands_free import Mode
from .about_dialog import AboutDialog
from .confirm_card import ConfirmCard
from .connections_dialog import ConnectionsDialog
from .drawer import Drawer
from .hud_panel import UNKNOWN, HudPanel
from .interpreter_window import InterpreterWindow
from .hud_widgets import CharacterSwitch, HoloButton, RingGauge, Waveform
from .memory_dialog import MemoryDialog
from .presence_gl import make_presence
from .professor_window import ProfessorWindow
from .remote_dialog import RemoteDialog
from .settings_dialog import SettingsDialog
from .style import dark_title_bar, keep_menus_in_fullscreen, restore_window_style, window_style

log = logging.getLogger(__name__)

_MIC_LOOK = {
    MicState.CLOSED: ("micOff", "MIC OFF"),
    MicState.OPENING: ("micOpening", "MIC OPENING"),
    MicState.OPEN: ("micOpen", "● MIC OPEN"),
    MicState.CLOSING: ("micOpening", "MIC CLOSING"),
}

_HANDS_FREE_LOOK = {
    Mode.WAITING: ("micLocal", "\u25cf MIC ON \u00b7 STAYS ON THIS PC"),
    Mode.REPLYING: ("micLocal", "\u25cf MIC ON \u00b7 STAYS ON THIS PC"),
    Mode.FOLLOW_UP: ("micLocal", "\u25cf MIC ON \u00b7 LISTENING FOR YOU"),
    Mode.TURN: ("micOpen", "\u25cf MIC OPEN \u00b7 SENDING TO GEMINI"),
}

COMPACT_SIZE = QSize(760, 520)
LEFT_DRAWER_WIDTH = 380
RIGHT_DRAWER_WIDTH = 460
EDGE = 16
REMINDER_CHECK_MS = 15_000
AD_CHECK_MS = 5_000
FIRST_WATCH_MS = 30_000
WATCH_EVERY_MS = 15 * 60_000
FILE_COMMAND = "/file "
FILE_COMMAND_HELP = "To create a file yourself, type: /file notes.txt | the text to put in it"


def _percent(value: float | None) -> str:
    return UNKNOWN if value is None else f"{value:.0f}%"


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings, hub: AudioHub, conversation: Conversation):
        super().__init__()
        self.settings = settings
        self._hub = hub
        self._conversation = conversation
        self._talking = False
        self._replying = False
        self._mic_state = MicState.CLOSED
        self._hands_free = Mode.OFF
        self._sharing = False
        self._view = "standard"
        self._maximized_before = True
        self._style_before = 0
        self._standard_size = QSize(980, 720)
        self._tray: QSystemTrayIcon | None = None
        self.setWindowTitle("APLHA")
        room = self.screen().availableGeometry()
        self.resize(min(980, int(room.width() * 0.8)), min(720, int(room.height() * 0.92)))
        icon = paths.assets_dir() / "alpha.ico"
        if icon.exists():
            self.setWindowIcon(QIcon(str(icon)))

        self.wordmark = QLabel(characters.CHARACTERS[settings.character].name)
        self.wordmark.setObjectName("wordmark")
        self.tagline = QLabel("Your Personal AI Assistant")
        self.tagline.setObjectName("tagline")
        self.mic_indicator = QLabel()
        self.mic_alert = QLabel()
        self.mic_alert.hide()
        self.sharing_indicator = QLabel()
        self.sharing_indicator.setObjectName("micOpen")
        self.sharing_indicator.hide()
        self.remote_indicator = QLabel()
        self.remote_indicator.setObjectName("micOpen")
        self.remote_indicator.hide()
        self.hands_free_button = QPushButton("Hands-free: off")
        self.hands_free_button.setCheckable(True)
        self.hands_free_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.hands_free_button.clicked.connect(self._hands_free_pressed)
        self.share_buttons = {}
        for source, label in (("screen", "Screen"), ("camera", "Camera")):
            button = QPushButton(label)
            button.setObjectName("share")
            button.setCheckable(True)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(lambda on, name=source: conversation.set_vision(name, on))
            self.share_buttons[source] = button
        self.view_button = QPushButton("More")
        self.view_button.setMenu(self._build_view_menu())
        self.memory_button = QPushButton("Memory")
        self.memory_button.clicked.connect(self.open_memory)
        self.settings_button = QPushButton("Settings")
        self.settings_button.clicked.connect(self.open_settings)

        self.presence = make_presence(settings.colours, settings.character)
        self.presence.set_level_source(lambda: max(conversation.mic.level, conversation.speaker.level))
        self.presence.set_voice_source(lambda: conversation.speaker.level)
        self.clock = QLabel()
        self.clock.setObjectName("hudClock")

        self.gauges = {name: RingGauge(name) for name in ("CPU", "MEMORY", "DISK")}
        self.system_panel = HudPanel("SYSTEM OVERVIEW")
        gauges = QHBoxLayout()
        for gauge in self.gauges.values():
            gauges.addWidget(gauge)
        self.system_panel.add_layout(gauges)
        self.system_panel.add_row("BATTERY", with_meter=True)
        self.system_panel.add_row("DOWN")
        self.system_panel.add_row("UP")

        self.files_list = QListWidget()
        self.files_list.setObjectName("files")
        self.files_list.setIconSize(QSize(36, 36))
        self.add_files_button = QPushButton("Add file")
        self.add_files_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.add_files_button.clicked.connect(self.choose_files)
        self.clear_files_button = QPushButton("Clear")
        self.clear_files_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.clear_files_button.clicked.connect(conversation.clear_attachments)
        file_buttons = QHBoxLayout()
        file_buttons.addWidget(self.add_files_button, 1)
        file_buttons.addWidget(self.clear_files_button)
        self.files_panel = HudPanel("FILES & PHOTOS")
        self.files_panel.setFixedWidth(LEFT_DRAWER_WIDTH)
        self.files_panel.add_widget(self.files_list)
        self.files_panel.add_layout(file_buttons)
        self.setAcceptDrops(True)

        self.link_panel = HudPanel("AI INTELLIGENCE")
        for name in ("MODEL", "GEMINI", "SPEECH", "MIC", "HANDS-FREE", "SCREEN", "CAMERA", "MEMORIES", "TOOLS"):
            self.link_panel.add_row(name)
        self.link_panel.set_value("MODEL", settings.model.removeprefix("gemini-"))
        self.link_panel.set_value("TOOLS", str(sum(len(tool["function_declarations"]) for tool in declarations())))

        self.character_switch = CharacterSwitch(settings.character)
        self.character_switch.chosen.connect(self.choose_character)

        self.stage_hole = QWidget()
        self.state_label = HoloButton("IDLE")
        self.message_button = HoloButton("MESSAGES")
        self.detail_label = QLabel("")
        self.detail_label.setObjectName("detail")
        self.detail_label.setTextFormat(Qt.TextFormat.PlainText)
        self.detail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail_label.setWordWrap(True)

        self.transcript = QPlainTextEdit()
        self.transcript.setReadOnly(True)
        self.transcript.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.transcript.setPlaceholderText(FILE_COMMAND_HELP)

        self.card = ConfirmCard(conversation.gate)
        self.card.confirmed.connect(conversation.confirm)
        self.card.cancelled.connect(conversation.cancel)

        self.input = QLineEdit()
        self.input.returnPressed.connect(self.send)
        self.send_button = QPushButton("Send")
        self.send_button.clicked.connect(self.send)
        self.undo_button = QPushButton("Undo")
        self.undo_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.undo_button.clicked.connect(conversation.undo)
        self._show_talk_key()
        self.waves = (Waveform(), Waveform())

        self.logo = QLabel()
        self.logo.setPixmap(self.windowIcon().pixmap(34, 34))
        self.readings = QLabel()
        self.readings.setObjectName("hudValue")
        self.brand_notch = QFrame()
        self.brand_notch.setObjectName("brandNotch")
        brand = QVBoxLayout(self.brand_notch)
        brand.setContentsMargins(46, 2, 46, 6)
        brand.setSpacing(0)
        brand.addWidget(self.wordmark, alignment=Qt.AlignmentFlag.AlignHCenter)
        brand.addWidget(self.tagline, alignment=Qt.AlignmentFlag.AlignHCenter)
        left_top = QHBoxLayout()
        left_top.addWidget(self.logo)
        left_top.addStretch(1)
        right_top = QHBoxLayout()
        right_top.addStretch(1)
        right_top.addWidget(self.clock)
        right_top.addSpacing(16)
        right_top.addWidget(self.readings)
        top = QHBoxLayout()
        top.addLayout(left_top, 1)
        top.addWidget(self.brand_notch)
        top.addLayout(right_top, 1)

        centre = QVBoxLayout()
        centre.addStretch(1)
        centre.addWidget(self.detail_label)
        centre.addWidget(self.card, 0, Qt.AlignmentFlag.AlignHCenter)
        self.stage_hole.setLayout(centre)

        typing = QHBoxLayout()
        typing.addWidget(self.input, 1)
        typing.addWidget(self.send_button)
        chat = QVBoxLayout()
        chat.addWidget(self.transcript)
        chat.addLayout(typing)
        commands = QGridLayout()
        commands.setSpacing(6)
        placed = (self.hands_free_button, *self.share_buttons.values(), self.memory_button, self.undo_button,
                  self.settings_button, self.view_button)
        commands.addWidget(placed[0], 0, 0, 1, 2)
        for index, button in enumerate(placed[1:3]):
            commands.addWidget(button, 0, 2 + index)
        for index, button in enumerate(placed[3:]):
            commands.addWidget(button, 1, index)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        bottom.addWidget(self.waves[0])
        bottom.addWidget(self.state_label)
        bottom.addWidget(self.message_button)
        bottom.addWidget(self.mic_alert)
        bottom.addWidget(self.sharing_indicator)
        bottom.addWidget(self.remote_indicator)
        bottom.addWidget(self.waves[1])
        bottom.addStretch(1)

        layout = QVBoxLayout(self.presence)
        layout.setContentsMargins(16, 10, 16, 12)
        layout.addLayout(top)
        layout.addWidget(self.stage_hole, 1)
        layout.addLayout(bottom)
        self.setCentralWidget(self.presence)

        self.left_drawer = Drawer("left", LEFT_DRAWER_WIDTH, self.presence)
        left = QVBoxLayout(self.left_drawer)
        left.setContentsMargins(0, 0, 0, 0)
        left.addWidget(self.mic_indicator, alignment=Qt.AlignmentFlag.AlignHCenter)
        left.addLayout(chat, 3)
        left.addWidget(self.files_panel, 2)
        self.right_drawer = Drawer("right", RIGHT_DRAWER_WIDTH, self.presence)
        readings = QVBoxLayout()
        readings.addWidget(self.system_panel)
        readings.addWidget(self.link_panel)
        panels = QHBoxLayout()
        panels.addLayout(readings)
        panels.addWidget(self.character_switch)
        right = QVBoxLayout(self.right_drawer)
        right.setContentsMargins(0, 0, 0, 0)
        right.addLayout(panels, 1)
        right.addLayout(commands)
        self.message_button.clicked.connect(lambda: self.left_drawer.set_open(not self.left_drawer.is_open))
        self.state_label.clicked.connect(lambda: self.right_drawer.set_open(not self.right_drawer.is_open))
        self.left_drawer.toggled.connect(lambda opened: self._drawer_moved(opened, self.right_drawer))
        self.right_drawer.toggled.connect(lambda opened: self._drawer_moved(opened, self.left_drawer))

        conversation.state_changed.connect(self.show_state)
        conversation.mic_changed.connect(self.show_mic)
        conversation.hands_free_changed.connect(self.show_hands_free)
        conversation.vision_changed.connect(self.show_sharing)
        conversation.heard.connect(self._append_heard)
        conversation.replied.connect(self._append_reply)
        conversation.reply_finished.connect(self._reply_finished)
        conversation.notice.connect(self._append_notice)
        conversation.proposal_ready.connect(self.card.present)
        conversation.proposal_ready.connect(self._close_drawers)
        conversation.proposal_resolved.connect(self.card.dismiss)
        conversation.link_changed.connect(self._show_link)
        conversation.attachments_changed.connect(self.show_attachments)
        conversation.watch_found.connect(self.show_watch_result)
        conversation.quit_requested.connect(self._quit)
        conversation.reminder_due.connect(self.show_reminder)
        conversation.class_requested.connect(self.open_class)
        conversation.class_message.connect(self._to_class)
        conversation.interpreter_requested.connect(self.open_interpreter)
        self._interpreter_window: InterpreterWindow | None = None
        self._class_window: ProfessorWindow | None = None
        self._remote =remote.RemoteServer(remote.RemoteControl(self._phone_said, conversation.interrupt))
        to_phone = self._remote.control.push
        conversation.replied.connect(lambda text: to_phone("reply", text))
        conversation.reply_finished.connect(lambda: to_phone("done"))
        conversation.notice.connect(lambda text: to_phone("notice", text))
        conversation.state_changed.connect(lambda state, detail: to_phone("state", detail or state.label))
        conversation.proposal_ready.connect(lambda _proposal: to_phone("notice", "This is waiting for CONFIRM on your PC."))
        self.show_mic(MicState.CLOSED)
        self.show_hands_free(Mode.OFF)
        self.show_sharing(set())
        self._show_speech_engine()
        self._linked = False
        self._show_link(False)
        self._tray = self._build_tray()
        self._telemetry = Telemetry()
        self._ticker = QTimer(self)
        self._ticker.setInterval(1000)
        self._ticker.timeout.connect(self.refresh_panels)
        self._ticker.start()
        self._wave_timer = QTimer(self)
        self._wave_timer.setInterval(50)
        self._wave_timer.timeout.connect(self._move_waves)
        self._wave_timer.start()
        self._watch_timer = QTimer(self)
        self._watch_timer.setInterval(FIRST_WATCH_MS)
        self._watch_timer.timeout.connect(self._check_watches)
        self._watch_timer.start()
        self._reminder_timer = QTimer(self)
        self._reminder_timer.setInterval(REMINDER_CHECK_MS)
        self._reminder_timer.timeout.connect(self._conversation.check_reminders)
        self._reminder_timer.start()
        self._ad_timer = QTimer(self)
        self._ad_timer.setInterval(AD_CHECK_MS)
        self._ad_timer.timeout.connect(self._check_for_ads)
        self._ad_timer.start()
        self._last_level = 0.0
        self.refresh_panels()
        QApplication.instance().installEventFilter(self)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        dark_title_bar(self)
        QTimer.singleShot(0, self._arrange)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        QTimer.singleShot(0, self._arrange)

    def _arrange(self) -> None:
        hole = self.stage_hole.geometry()
        self.presence.focus_x = hole.center().x()
        middle = hole.center().y()
        reach = self.presence.height() - hole.top() - EDGE
        self.left_drawer.place(QRect(EDGE, hole.top(), LEFT_DRAWER_WIDTH, reach), middle)
        area = QRect(hole.right() - RIGHT_DRAWER_WIDTH + 1, hole.top(), RIGHT_DRAWER_WIDTH, reach)
        self.right_drawer.place(area, middle)

    def _drawer_moved(self, opened: bool, other: Drawer) -> None:
        """A drawer never covers what is live, and on a small window one opens at a time."""
        for label in (self.state_label, self.message_button, self.mic_alert, self.sharing_indicator, self.remote_indicator):
            label.raise_()
        if opened and self.width() < LEFT_DRAWER_WIDTH + RIGHT_DRAWER_WIDTH + 60:
            other.set_open(False)

    def _close_drawers(self, *_ignored) -> None:
        """Nothing may sit over a CONFIRM card."""
        self.left_drawer.set_open(False)
        self.right_drawer.set_open(False)

    # Showing what is happening.

    def refresh_panels(self) -> None:
        """Only measures while someone can see the result."""
        self._show_remote()
        if not self.isVisible() or self.isMinimized() or self._view == "compact":
            return
        self.clock.setText(datetime.now().strftime("%H:%M:%S"))
        now = self._telemetry.read()
        panel = self.system_panel
        self.readings.setText(f"CPU {_percent(now.cpu)}   RAM {_percent(now.memory)}")
        self.gauges["CPU"].set_value(now.cpu)
        self.gauges["MEMORY"].set_value(now.memory)
        self.gauges["DISK"].set_value(now.disk)
        battery = UNKNOWN if now.battery is None else f"{now.battery:.0f}%{' +' if now.charging else ''}"
        panel.set_value("BATTERY", battery, now.battery)
        panel.set_value("DOWN", speed_text(now.down_kb))
        panel.set_value("UP", speed_text(now.up_kb))
        self.link_panel.set_value("MEMORIES", str(self._conversation.memory.count()))

    def _move_waves(self) -> None:
        """Bars follow the true sound level, and nothing is redrawn during silence."""
        level = max(self._conversation.mic.level, self._conversation.speaker.level)
        if (level or self._last_level) and self.isVisible() and not self.isMinimized():
            for wave in self.waves:
                wave.set_level(level)
        self._last_level = level

    def choose_files(self) -> None:
        chosen, _ = QFileDialog.getOpenFileNames(self, "Send with your next message")
        self.add_files(chosen)

    def add_files(self, names: list[str]) -> None:
        for name in names:
            try:
                self._conversation.add_attachment(attachments.load(Path(name)))
            except (ValueError, OSError) as error:
                self._append_notice(str(error))

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        self.add_files([url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()])

    def show_attachments(self, items: list) -> None:
        self.files_list.clear()
        for item in items:
            row = QListWidgetItem(item.name)
            if item.is_photo:
                row.setIcon(QIcon(QPixmap.fromImage(QImage.fromData(item.photo))))
            self.files_list.addItem(row)
        if items:
            self.left_drawer.set_open(True)

    def _hands_free_pressed(self, wanted: bool) -> None:
        """The owner's last choice is kept for the next start."""
        self.settings.hands_free_at_start = wanted
        settings_store.save(self.settings)
        self._conversation.set_hands_free(wanted)

    def show_reminder(self, text: str) -> None:
        self._append_notice(f"Reminder: {text}")
        QApplication.beep()
        if self._tray is not None:
            self._tray.showMessage("APLHA reminder", text)

    def maybe_brief(self, now: datetime) -> None:
        """Once a day, on the first start with everything set up."""
        today = now.strftime("%Y-%m-%d")
        if self.settings.morning_briefing and self.settings.setup_complete and self.settings.last_briefing != today:
            self.settings.last_briefing = today
            settings_store.save(self.settings)
            self._conversation.give_briefing()

    def _quit(self) -> None:
        QApplication.instance().quit()

    def _check_watches(self) -> None:
        self._watch_timer.setInterval(WATCH_EVERY_MS)
        self._conversation.check_watches()

    def show_watch_result(self, found) -> None:
        """The page's text comes only here, to the owner, and never reaches the model."""
        message = found.problem or "\n".join(found.lines)
        self._append_notice(f"{found.name}: {message}")
        if not found.problem:
            QApplication.beep()
            self.left_drawer.set_open(True)
        if self._tray is not None:
            self._tray.showMessage(f"APLHA: {found.name}", message[:300])

    def choose_character(self, character_id: str) -> None:
        self.settings.character = character_id
        settings_store.save(self.settings)
        self.presence.set_character(character_id)
        self.wordmark.setText(characters.CHARACTERS[character_id].name)
        self._conversation.settings_changed()

    def choose_character_image(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(self, "Picture for this character", "", "Pictures (*.png *.jpg *.jpeg *.webp)")
        if not chosen:
            return
        try:
            characters.add_image(self.settings.character, Path(chosen))
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, "APLHA", str(error))
            return
        self._show_new_picture()

    def _show_speech_engine(self) -> None:
        self.link_panel.set_value("SPEECH", "ON THIS PC" if self.settings.speech_engine == "offline" else "GEMINI")

    def _show_link(self, linked: bool) -> None:
        self._linked = linked
        self.link_panel.set_value("GEMINI", "LIVE" if linked else "STANDBY")

    def show_state(self, state: State, detail: str) -> None:
        self.presence.set_state(state)
        tint = self.settings.colour_for(state.value)
        colour = QColor(tint)
        for panel in (self.system_panel, self.files_panel, self.link_panel):
            panel.set_tint(tint)
        for gauge in self.gauges.values():
            gauge.set_tint(colour)
        for wave in self.waves:
            wave.tint = colour
            wave.update()
        self.character_switch.set_tint(colour)
        self.state_label.setText(state.label.upper())
        self.state_label.set_tint(colour)
        self.message_button.set_tint(colour)
        self.detail_label.setText(detail)

    def show_mic(self, state: MicState) -> None:
        self._mic_state = state
        look = _MIC_LOOK[state]
        if self._hands_free is not Mode.OFF and state is not MicState.CLOSED:
            look = _HANDS_FREE_LOOK[self._hands_free]
        stays_here = self.settings.speech_engine == "offline" or self._conversation.in_class
        if state is MicState.OPEN and self._hands_free is Mode.TURN and stays_here:
            look = ("micOpen", "\u25cf MIC OPEN \u00b7 STAYS ON THIS PC")
        elif state is MicState.OPEN and self._hands_free is Mode.OFF:
            where = "STAYS ON THIS PC" if stays_here else "SENDING TO GEMINI"
            look = ("micOpen", f"\u25cf MIC OPEN \u00b7 {where}")
        self._paint_mic(*look)
        self.link_panel.set_value("MIC", "OFF" if state is MicState.CLOSED else "ON")
        self._update_tray()

    def show_hands_free(self, mode: Mode) -> None:
        self._hands_free = mode
        on = mode is not Mode.OFF
        self.hands_free_button.setChecked(on)
        self.hands_free_button.setText("Hands-free: ON" if on else "Hands-free: off")
        self.link_panel.set_value("HANDS-FREE", "ON" if on else "OFF")
        self.show_mic(self._mic_state)

    def show_sharing(self, sources: set) -> None:
        for source, button in self.share_buttons.items():
            on = source in sources
            button.setChecked(on)
            button.setText(f"\u25cf {source.upper()} SHARED" if on else source.capitalize())
            self.link_panel.set_value(source.upper(), "SHARED" if on else "OFF")
        self._sharing = bool(sources)
        self.sharing_indicator.setText(f"\u25cf {' + '.join(sorted(sources)).upper()} SHARED")
        self.sharing_indicator.setVisible(self._sharing)
        self._update_tray()

    def _paint_mic(self, name: str, text: str) -> None:
        """The full wording lives in the drawer. The main screen always shows that the mic is live:
        a tiny dot while sound stays on this PC, and the full pill whenever it is leaving."""
        local = name == "micLocal"
        for label, label_name, label_text in (
            (self.mic_indicator, name, text),
            (self.mic_alert, "micDot" if local else name, "\u25cf" if local else text),
        ):
            label.setObjectName(label_name)
            label.setText(label_text)
            label.style().unpolish(label)
            label.style().polish(label)
        self.mic_alert.setToolTip(text)
        self.mic_alert.setVisible(self._mic_state is not MicState.CLOSED)

    def _show_remote(self) -> None:
        """Someone reaching this PC from another device must always show on the main screen."""
        words = "PHONE CONNECTED" if self._remote.control.connected() else "PHONE CONTROL ON"
        self.remote_indicator.setText(f"● {words}")
        self.remote_indicator.setVisible(self._remote.running)

    def _add_line(self, text: str) -> None:
        self._replying = False
        self.transcript.appendPlainText(text)

    def _append_heard(self, text: str) -> None:
        self._add_line(f"You (heard): {text}")

    def _append_reply(self, text: str) -> None:
        if not self._replying:
            self.transcript.appendPlainText(f"{self.wordmark.text()}: ")
            self._replying = True
        cursor = self.transcript.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(text)
        self.transcript.setTextCursor(cursor)

    def _reply_finished(self) -> None:
        self._replying = False

    def _append_notice(self, text: str) -> None:
        self._add_line(f"[{text}]")

    # Input.

    def send(self) -> None:
        text = self.input.text().strip()
        if not text:
            return
        self.input.clear()
        if text == "/file" or text.startswith(FILE_COMMAND):
            self._file_command(text)
            return
        self._add_line(f"You: {text}")
        self._conversation.send_text(text)

    def _file_command(self, text: str) -> None:
        name, separator, content = text[len("/file") :].partition("|")
        if not separator or not name.strip():
            self._append_notice(FILE_COMMAND_HELP)
            return
        self._conversation.propose_file(name.strip(), content.strip())

    def _show_talk_key(self) -> None:
        self.input.setPlaceholderText(f"Type a message, or hold {self.settings.talk_key} to talk")

    def _talk_down(self) -> None:
        if not self._talking:
            self._talking = True
            self._conversation.talk_pressed()

    def _talk_up(self) -> None:
        if self._talking:
            self._talking = False
            self._conversation.talk_released()

    def eventFilter(self, watched, event) -> bool:
        kind = event.type()
        if kind == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape and self._window_in_charge():
            if self._conversation.is_replying:
                self._conversation.interrupt()
            else:
                self.fullscreen_action.setChecked(False)
            return True
        if kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            if not event.isAutoRepeat() and self._is_talk_key(event) and self._window_in_charge():
                if kind == QEvent.Type.KeyPress:
                    self._talk_down()
                else:
                    self._talk_up()
                return True
        elif kind in (QEvent.Type.ApplicationDeactivate, QEvent.Type.WindowDeactivate):
            # A key release is never delivered once focus has moved away.
            if kind == QEvent.Type.ApplicationDeactivate or watched is self:
                self._talk_up()
        return False

    def _is_talk_key(self, event) -> bool:
        return QKeySequence(event.key()).toString() == self.settings.talk_key

    def _window_in_charge(self) -> bool:
        return QApplication.activeWindow() is self

    # Window modes.

    def _build_view_menu(self) -> QMenu:
        menu = QMenu(self)
        self.compact_action = QAction("Compact", self, checkable=True)
        self.compact_action.toggled.connect(self.set_compact)
        self.fullscreen_action = QAction("Fullscreen", self, checkable=True, shortcut="F11")
        self.fullscreen_action.toggled.connect(self.set_fullscreen)
        self.addAction(self.fullscreen_action)
        self.about_action = QAction("About me", self)
        self.about_action.triggered.connect(self.open_about)
        self.briefing_action = QAction("Morning briefing", self, checkable=True, checked=self.settings.morning_briefing)
        self.briefing_action.toggled.connect(self.set_briefing)
        self.skip_ads_action = QAction("Skip YouTube ads automatically", self, checkable=True, checked=self.settings.skip_ads)
        self.skip_ads_action.toggled.connect(self.set_skip_ads)
        self.autostart_action = QAction("Start with Windows", self, checkable=True, checked=autostart.is_on())
        self.autostart_action.toggled.connect(self.set_autostart)
        self.top_action = QAction("Always on top", self, checkable=True)
        self.top_action.toggled.connect(self.set_always_on_top)
        self.tray_action = QAction("Hide to tray", self)
        self.tray_action.triggered.connect(self.hide_to_tray)
        self.image_action = QAction("Set this character's picture", self)
        self.image_action.triggered.connect(self.choose_character_image)
        self.connections_action = QAction("Connections (Notion, Gmail, Grok)", self)
        self.connections_action.triggered.connect(self.open_connections)
        self.class_action = QAction("Professor Mode", self)
        self.class_action.triggered.connect(self.open_class)
        self.interpreter_action = QAction("Live translation", self)
        self.interpreter_action.triggered.connect(lambda: self.open_interpreter(""))
        self.remote_action = QAction("Phone and tablet control", self, checkable=True)
        self.remote_action.toggled.connect(self.set_remote)
        self.remote_code_action = QAction("New phone code", self, enabled=False)
        self.remote_code_action.triggered.connect(self.show_remote_code)
        self.remove_image_action =QAction("Remove my picture (back to default)", self)
        self.remove_image_action.triggered.connect(self._ask_to_remove_image)
        self.restore_image_action = QAction("Bring back my removed picture", self)
        self.restore_image_action.triggered.connect(self.restore_character_image)
        menu.aboutToShow.connect(self._update_image_actions)
        for action in (self.about_action, self.connections_action, self.class_action, self.interpreter_action, self.remote_action, self.remote_code_action,
                       self.compact_action, self.fullscreen_action, self.top_action,
                       self.briefing_action, self.skip_ads_action, self.autostart_action, self.tray_action, self.image_action, self.remove_image_action, self.restore_image_action):
            menu.addAction(action)
        choices = menu.addMenu("Language")
        group = QActionGroup(self)
        self.language_actions = {}
        everything = QMenu("All languages", choices)
        everything.setStyleSheet("QMenu { menu-scrollable: 1; }")
        for code, name in SPEECH_LANGUAGES.items():
            action = QAction(name, self, checkable=True, checked=code == self.settings.speech_language)
            action.triggered.connect(lambda _on, chosen=code: self.choose_language(chosen))
            group.addAction(action)
            (choices if code in languages.MAIN_CHOICES else everything).addAction(action)
            self.language_actions[code] = action
        choices.addMenu(everything)
        return menu

    def set_remote(self, on: bool) -> None:
        """Off forgets every device, so each new start needs a fresh code."""
        if on:
            try:
                self._remote.start()
            except OSError as error:
                self._append_notice(f"Phone control could not start: {type(error).__name__}")
                self.remote_action.setChecked(False)
                return
        else:
            self._remote.stop()
        self.remote_code_action.setEnabled(on)
        self._show_remote()
        self._update_tray()
        if on:
            self.show_remote_code()

    def show_remote_code(self) -> None:
        links = [f"http://{place}:{self._remote.port}" for place in remote.addresses()]
        self._remote_dialog = RemoteDialog(links, self._remote.control.new_code(), self)
        self._remote_dialog.show()

    def _phone_said(self, text: str) -> None:
        self._conversation.heard.emit(f"(from phone) {text}")
        self._conversation.send_text(text)

    def open_class(self) -> None:
        if self._class_window is None:
            self._class_window = ProfessorWindow(self._conversation, self)
        self._class_window.start_class()

    def open_interpreter(self, target: str) -> None:
        if self._interpreter_window is None:
            self._interpreter_window = InterpreterWindow(self._conversation, self.settings, self)
        self._interpreter_window.open_with(target)

    def _to_class(self, text: str) -> None:
        if self._class_window is not None:
            self._class_window.handle(text)

    def set_skip_ads(self, on: bool) -> None:
        self.settings.skip_ads = on
        settings_store.save(self.settings)

    def _check_for_ads(self) -> None:
        if self.settings.skip_ads:
            self._conversation.check_for_ads()

    def set_briefing(self, on: bool) -> None:
        self.settings.morning_briefing = on
        settings_store.save(self.settings)

    def set_autostart(self, on: bool) -> None:
        try:
            autostart.turn_on() if on else autostart.turn_off()
        except (OSError, subprocess.SubprocessError) as error:
            self._append_notice(f"Start with Windows could not be changed: {type(error).__name__}")

    def choose_language(self, code: str) -> None:
        """The next reply uses it, because the session restarts with the new language."""
        self.settings.speech_language = code
        settings_store.save(self.settings)
        self._append_notice(languages.availability(code))
        self._conversation.settings_changed()

    def _update_image_actions(self) -> None:
        chosen = self.settings.character
        self.remove_image_action.setEnabled(characters.has_own_picture(chosen))
        self.restore_image_action.setEnabled(characters.can_restore_image(chosen))

    def _ask_to_remove_image(self) -> None:
        name = characters.CHARACTERS[self.settings.character].name
        question = f"Go back to {name}'s default picture? You can bring yours back from this menu."
        answer = QMessageBox.question(self, "APLHA", question)
        if answer == QMessageBox.StandardButton.Yes:
            self.remove_character_image()

    def remove_character_image(self) -> None:
        if characters.remove_image(self.settings.character):
            self._show_new_picture()

    def restore_character_image(self) -> None:
        if characters.restore_image(self.settings.character):
            self._show_new_picture()

    def _show_new_picture(self) -> None:
        self.presence.set_character(self.settings.character)
        self.character_switch.tiles[self.settings.character].refresh()

    def set_compact(self, on: bool) -> None:
        self._view = "compact" if on else "standard"
        if on:
            self._standard_size = self.size()
        self._close_drawers()
        for widget in self._compact_hides():
            widget.setVisible(not on)
        self.centralWidget().layout().activate()
        self.resize(COMPACT_SIZE if on else self._standard_size)
        self.refresh_panels()

    def _compact_hides(self) -> tuple:
        return (self.brand_notch,)

    def set_fullscreen(self, on: bool) -> None:
        if on:
            self._maximized_before = self.isMaximized()
            self._style_before = window_style(self)
            self.showFullScreen()
            keep_menus_in_fullscreen(self)
            return
        self.showNormal()
        restore_window_style(self, self._style_before)
        if self._maximized_before:
            self.showMaximized()

    def set_always_on_top(self, on: bool) -> None:
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on)
        self.show()

    def _build_tray(self) -> QSystemTrayIcon | None:
        self.tray_action.setEnabled(QSystemTrayIcon.isSystemTrayAvailable())
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return None
        menu = QMenu(self)
        menu.addAction("Show APLHA", self.show_from_tray)
        menu.addAction("Quit", QApplication.instance().quit)
        tray = QSystemTrayIcon(self.windowIcon(), self)
        tray.setContextMenu(menu)
        tray.activated.connect(lambda _reason: self.show_from_tray())
        tray.show()
        self._tray = tray
        self._update_tray()
        return tray

    def _live_inputs(self) -> str:
        """What is on right now. The tray must show this, even with the window hidden."""
        live = []
        if self._mic_state is not MicState.CLOSED:
            live.append("microphone")
        if self._sharing:
            live.append("screen or camera sharing")
        if self._remote.running:
            live.append("phone control")
        return " and ".join(live)

    def _update_tray(self) -> None:
        if self._tray is None:
            return
        live = self._live_inputs()
        self._tray.setToolTip(f"APLHA: {live} ON" if live else "APLHA")
        icon = self.windowIcon()
        if live:
            picture = icon.pixmap(32, 32) if not icon.isNull() else QPixmap(32, 32)
            painter = QPainter(picture)
            painter.setBrush(QColor("#e0243a"))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(16, 16, 15, 15)
            painter.end()
            icon = QIcon(picture)
        self._tray.setIcon(icon)

    def hide_to_tray(self) -> None:
        if self._tray is not None:
            self.hide()

    def show_from_tray(self) -> None:
        self.showNormal()
        self.activateWindow()

    # Settings.

    def open_about(self) -> None:
        dialog = AboutDialog(self)
        if dialog.exec():
            self._conversation.settings_changed()
        dialog.deleteLater()

    def open_connections(self) -> None:
        dialog = ConnectionsDialog(self)
        dialog.exec()
        dialog.deleteLater()

    def open_memory(self) -> None:
        dialog = MemoryDialog(self._conversation.memory, self)
        dialog.exec()
        dialog.deleteLater()

    def open_settings(self, first_run: bool = False) -> None:
        dialog = SettingsDialog(self.settings, self._hub, self._conversation, first_run, self)
        accepted = dialog.exec()
        dialog.deleteLater()
        if accepted:
            self.apply_settings(dialog.result_settings())
        elif first_run:
            self._conversation.settings_changed()

    def apply_settings(self, chosen: Settings) -> None:
        self.settings.__dict__.update(chosen.__dict__)
        settings_store.save(self.settings)
        self.presence.set_colours(self.settings.colours)
        self._show_talk_key()
        self._show_speech_engine()
        self.language_actions[self.settings.speech_language].setChecked(True)
        self.link_panel.set_value("MODEL", self.settings.model.removeprefix("gemini-"))
        self._conversation.settings_changed()

    def closeEvent(self, event) -> None:
        self._talk_up()
        self._remote.stop()
        if self._class_window is not None:
            self._class_window.end_class()
        if self._interpreter_window is not None:
            self._interpreter_window.close()
        if self._tray is not None:
            self._tray.hide()
        super().closeEvent(event)
