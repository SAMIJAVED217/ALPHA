"""The form where the owner tells APLHA about themselves."""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QFormLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget

from .. import profile
from .style import dark_title_bar

_ONE_LINE = ("name", "city", "languages")


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("About me")
        self.setMinimumSize(560, 560)
        heading = QLabel("About me")
        heading.setObjectName("heading")
        note = QLabel(
            "Share only what you want. It stays in a file on this computer, and is sent to Gemini at the start "
            "of each conversation so APLHA can personalise its help. Avoid passwords and ID numbers."
        )
        note.setObjectName("detail")
        note.setWordWrap(True)
        saved = profile.load()
        self.fields: dict[str, QLineEdit | QPlainTextEdit] = {}
        form = QFormLayout()
        for key, label in profile.FIELDS.items():
            field = QLineEdit(saved.get(key, "")) if key in _ONE_LINE else QPlainTextEdit(saved.get(key, ""))
            if isinstance(field, QPlainTextEdit):
                field.setFixedHeight(64)
                field.setTabChangesFocus(True)
            self.fields[key] = field
            form.addRow(label, field)
        holder = QWidget()
        holder.setLayout(form)
        scroller = QScrollArea()
        scroller.setWidget(holder)
        scroller.setWidgetResizable(True)
        save = QPushButton("Save and close")
        save.clicked.connect(self._save)
        layout = QVBoxLayout(self)
        layout.addWidget(heading)
        layout.addWidget(note)
        layout.addWidget(scroller, 1)
        layout.addWidget(save)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        dark_title_bar(self)

    def _answer(self, field) -> str:
        return field.text() if isinstance(field, QLineEdit) else field.toPlainText()

    def _save(self) -> None:
        profile.save({key: self._answer(field) for key, field in self.fields.items()})
        self.accept()
