"""The live translation window: the language to speak, and both sides of what was said."""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout

from .. import languages, settings as settings_store
from .style import dark_title_bar


class InterpreterWindow(QDialog):
    def __init__(self, conversation, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Live translation")
        self.resize(760, 520)
        self._conversation = conversation
        self._settings = settings

        heading = QLabel("Live translation")
        heading.setObjectName("heading")
        self.status = QLabel("Choose the language to speak. The language being spoken is found automatically.")
        self.status.setObjectName("detail")
        self.status.setWordWrap(True)
        self.language = QComboBox()
        for code, name in languages.INTERPRETER_LANGUAGES.items():
            self.language.addItem(name, code)
        chosen = self.language.findData(settings.translate_to)
        self.language.setCurrentIndex(max(chosen, 0))
        self.start_button = QPushButton("Start")
        self.start_button.setAutoDefault(False)
        self.start_button.clicked.connect(self._toggle)
        self.original = QPlainTextEdit()
        self.original.setReadOnly(True)
        self.original.setPlaceholderText("What was said appears here")
        self.translation = QPlainTextEdit()
        self.translation.setReadOnly(True)
        self.translation.setPlaceholderText("The translation appears here")

        top = QHBoxLayout()
        top.addWidget(QLabel("Speak in"))
        top.addWidget(self.language, 1)
        top.addWidget(self.start_button)
        layout = QVBoxLayout(self)
        layout.addWidget(heading)
        layout.addWidget(self.status)
        layout.addLayout(top)
        layout.addWidget(self.original, 1)
        layout.addWidget(self.translation, 1)

        conversation.interpreting_changed.connect(self._show_running)
        conversation.interpreter_heard.connect(lambda text: self._add(self.original, text))
        conversation.interpreter_said.connect(lambda text: self._add(self.translation, text))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        dark_title_bar(self)

    def closeEvent(self, event) -> None:
        self._conversation.stop_interpreting()
        super().closeEvent(event)

    def reject(self) -> None:
        self._conversation.stop_interpreting()
        super().reject()

    def open_with(self, target: str) -> None:
        """A language means start straight away. Without one, the owner chooses first."""
        self.show()
        self.raise_()
        if target:
            self.language.setCurrentIndex(max(self.language.findData(target), 0))
            self._start()

    def _toggle(self) -> None:
        if self._conversation.interpreting:
            self._conversation.stop_interpreting()
        else:
            self._start()

    def _start(self) -> None:
        if self._conversation.in_class:
            self.status.setText("End the class first, because both use the microphone.")
            return
        self._settings.translate_to = self.language.currentData()
        settings_store.save(self._settings)
        self.original.clear()
        self.translation.clear()
        self._conversation.start_interpreting(self.language.currentData())

    def _show_running(self, target: str) -> None:
        running = bool(target)
        self.start_button.setText("Stop" if running else "Start")
        self.language.setEnabled(not running)
        if running:
            name = languages.INTERPRETER_LANGUAGES.get(target, target)
            self.status.setText(f"Listening, and speaking in {name}. Your sound goes to Google while this runs.")
        else:
            self.status.setText("Stopped. The microphone is closed.")

    @staticmethod
    def _add(view: QPlainTextEdit, text: str) -> None:
        cursor = view.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(text)
        view.setTextCursor(cursor)
