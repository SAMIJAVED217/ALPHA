"""Settings, which double as first-run setup."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from .. import keystore, languages
from ..audio.hub import AudioHub, DeviceList
from ..settings import DEFAULT_MODEL, SPEECH_LANGUAGES, TALK_KEYS, Settings
from ..state import DEFAULT_COLOURS, State
from ..voice.conversation import Conversation
from .connections_dialog import ConnectionsDialog
from .style import dark_title_bar

SYSTEM_DEFAULT = "System default"


class SettingsDialog(QDialog):
    _devices_ready = Signal(object)

    def __init__(self, settings: Settings, hub: AudioHub, conversation: Conversation, first_run: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Welcome to APLHA" if first_run else "APLHA settings")
        self.setMinimumWidth(520)
        self._original = settings
        self.colours = dict(settings.colours)
        self._hub = hub
        self._conversation = conversation

        heading = QLabel("Let's set up APLHA" if first_run else "Settings")
        heading.setObjectName("heading")
        intro = QLabel(
            "APLHA needs a Gemini key to talk. It is kept in the Windows Credential "
            "Manager, never in a file on disk."
        )
        intro.setWordWrap(True)
        intro.setObjectName("detail")

        self.key_field = QLineEdit()
        self.key_field.setEchoMode(QLineEdit.EchoMode.Password)
        self._has_saved_key = self._saved_key_exists()
        self.key_field.setPlaceholderText(
            "A key is saved. Type here only to replace it." if self._has_saved_key else "Paste your Gemini key"
        )
        self.check_button = QPushButton("Check key")
        self.check_button.clicked.connect(self._check_key)
        self.key_status = QLabel("")
        self.key_status.setObjectName("keyStatus")
        self.key_status.setWordWrap(True)
        key_row = QHBoxLayout()
        key_row.addWidget(self.key_field, 1)
        key_row.addWidget(self.check_button)

        self.model_field = QLineEdit(settings.model)
        self.microphone_box = QComboBox()
        self.speakers_box = QComboBox()
        self.test_button = QPushButton("Test")
        self.test_button.clicked.connect(self._test_speakers)
        speakers_row = QHBoxLayout()
        speakers_row.addWidget(self.speakers_box, 1)
        speakers_row.addWidget(self.test_button)
        self.device_status = QLabel("Looking for sound devices...")
        self.device_status.setObjectName("detail")
        self.talk_key_box = QComboBox()
        self.talk_key_box.addItems(TALK_KEYS)
        self.talk_key_box.setCurrentText(settings.talk_key)

        self._add_device(self.microphone_box, SYSTEM_DEFAULT, "")
        self._add_device(self.speakers_box, SYSTEM_DEFAULT, "")
        self._restore(self.microphone_box, settings.microphone)
        self._restore(self.speakers_box, settings.speakers)

        form = QFormLayout()
        form.addRow("Gemini key", key_row)
        form.addRow("", self.key_status)
        form.addRow("Model", self.model_field)
        form.addRow("Microphone", self.microphone_box)
        form.addRow("Speakers", speakers_row)
        form.addRow("", self.device_status)
        form.addRow("Hold to talk key", self.talk_key_box)
        self.speech_box = QComboBox()
        self.speech_box.addItem("Gemini voice (online)", "gemini")
        self.speech_box.addItem("Offline voice (your voice stays on this PC)", "offline")
        self.speech_box.setCurrentIndex(self.speech_box.findData(settings.speech_engine))
        form.addRow("Speech", self.speech_box)
        self.language_box = QComboBox()
        for code, name in SPEECH_LANGUAGES.items():
            self.language_box.addItem(name, code)
        self.language_box.setCurrentIndex(self.language_box.findData(settings.speech_language))
        self.language_note = QLabel()
        self.language_note.setObjectName("detail")
        self.language_note.setWordWrap(True)
        self.language_box.currentIndexChanged.connect(self._show_language_note)
        self._show_language_note()
        form.addRow("Language", self.language_box)
        form.addRow("", self.language_note)

        self._swatches: dict[str, QPushButton] = {}
        colour_grid = QGridLayout()
        for index, state in enumerate(State):
            swatch = QPushButton(state.label)
            swatch.clicked.connect(lambda _checked=False, name=state.value: self._pick_colour(name))
            self._swatches[state.value] = swatch
            colour_grid.addWidget(swatch, index // 3, index % 3)
        connections = QPushButton("Connections")
        connections.clicked.connect(self._open_connections)
        reset = QPushButton("Reset colours")
        reset.clicked.connect(self._reset_colours)
        self._paint_swatches()

        self.save_button = QPushButton("Finish" if first_run else "Save")
        self.save_button.clicked.connect(self._save)
        cancel = QPushButton("Skip for now" if first_run else "Cancel")
        cancel.clicked.connect(self.reject)
        actions = QHBoxLayout()
        actions.addWidget(reset)
        actions.addWidget(connections)
        actions.addStretch(1)
        actions.addWidget(cancel)
        actions.addWidget(self.save_button)

        layout = QVBoxLayout(self)
        layout.addWidget(heading)
        layout.addWidget(intro)
        layout.addLayout(form)
        layout.addWidget(QLabel("Colours"))
        layout.addLayout(colour_grid)
        layout.addLayout(actions)

        conversation.key_checked.connect(self._key_checked)
        self._devices_ready.connect(self._fill_devices)
        hub.list_devices().add_done_callback(self._devices_done)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        dark_title_bar(self)

    def _saved_key_exists(self) -> bool:
        try:
            return keystore.has_key()
        except keystore.KeyStoreError:
            return False

    # Sound devices. The list arrives from the audio thread.

    def _devices_done(self, future) -> None:
        try:
            self._devices_ready.emit(future.result())
        except RuntimeError:
            pass  # The dialog closed first.
        except Exception as error:
            self._devices_ready.emit(error)

    def _fill_devices(self, result) -> None:
        if isinstance(result, Exception):
            self.device_status.setText(f"Sound devices could not be listed: {result}")
            return
        assert isinstance(result, DeviceList)
        self._fill_box(self.microphone_box, [device.name for device in result.inputs])
        self._fill_box(self.speakers_box, [device.name for device in result.outputs])
        self.device_status.setText(f"Found {len(result.inputs)} microphones and {len(result.outputs)} speakers.")

    def _fill_box(self, box: QComboBox, names: list[str]) -> None:
        chosen = box.currentData()
        box.clear()
        self._add_device(box, SYSTEM_DEFAULT, "")
        for name in names:
            self._add_device(box, name, name)
        self._restore(box, chosen)

    @staticmethod
    def _add_device(box: QComboBox, label: str, name: str) -> None:
        box.addItem(label, name)

    def _restore(self, box: QComboBox, name: str) -> None:
        index = box.findData(name)
        if index < 0:
            box.addItem(f"{name} (not connected)", name)
            index = box.count() - 1
        box.setCurrentIndex(index)

    def _test_speakers(self) -> None:
        self._conversation.speaker.play_test_tone(self.speakers_box.currentData())

    # The key.

    def _check_key(self) -> None:
        key = self.key_field.text().strip()
        if not key:
            try:
                key = keystore.load_key()
            except keystore.KeyStoreError as error:
                self.key_status.setText(str(error))
                return
        if not key:
            self.key_status.setText("Paste a key first.")
            return
        self.key_status.setText("Asking Gemini...")
        self.check_button.setEnabled(False)
        self._conversation.check_key(key, self.model_field.text().strip() or DEFAULT_MODEL)

    def _key_checked(self, accepted: bool, message: str) -> None:
        self.check_button.setEnabled(True)
        self.key_status.setText(message)

    def _open_connections(self) -> None:
        dialog = ConnectionsDialog(self)
        dialog.exec()
        dialog.deleteLater()

    # Colours.

    def _paint_swatches(self) -> None:
        for name, swatch in self._swatches.items():
            colour = self.colours[name]
            ink = "#000000" if QColor(colour).lightness() > 120 else "#ffffff"
            swatch.setStyleSheet(f"background: {colour}; color: {ink}; border-color: {colour};")

    def _pick_colour(self, name: str) -> None:
        chosen = QColorDialog.getColor(QColor(self.colours[name]), self, f"Colour for {name}")
        if chosen.isValid():
            self.colours[name] = chosen.name()
            self._paint_swatches()

    def _reset_colours(self) -> None:
        self.colours = dict(DEFAULT_COLOURS)
        self._paint_swatches()

    def _show_language_note(self) -> None:
        self.language_note.setText(languages.availability(self.language_box.currentData()))

    # Saving.

    def result_settings(self) -> Settings:
        return replace(
            self._original,
            setup_complete=True,
            model=self.model_field.text().strip() or DEFAULT_MODEL,
            microphone=self.microphone_box.currentData() or "",
            speakers=self.speakers_box.currentData() or "",
            talk_key=self.talk_key_box.currentText(),
            speech_engine=self.speech_box.currentData(),
            speech_language=self.language_box.currentData(),
            colours=dict(self.colours),
        )

    def _save(self) -> None:
        key = self.key_field.text().strip()
        if key:
            try:
                keystore.save_key(key)
            except keystore.KeyStoreError as error:
                QMessageBox.warning(self, "APLHA", str(error))
                return
            self.key_field.clear()
        self.accept()
