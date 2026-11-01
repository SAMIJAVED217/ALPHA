"""Connect Notion, Gmail and Grok. Everything entered here goes to the Credential Manager."""

from __future__ import annotations

import threading

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QDialog, QFormLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout

from .. import keystore
from ..tools import gmail, grok, notion
from .style import dark_title_bar


class ConnectionsDialog(QDialog):
    _gmail_done = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("APLHA connections")
        self.setMinimumWidth(520)

        heading = QLabel("Connections")
        heading.setObjectName("heading")
        note = QLabel("Secrets go to the Windows Credential Manager, never to a file.")
        note.setObjectName("detail")

        self.notion_token = self._secret_field(notion.TOKEN_ACCOUNT, "Paste your Notion integration token")
        self.grok_key = self._secret_field(grok.KEY_ACCOUNT, "Paste your xAI API key")
        self.gmail_id = QLineEdit(keystore.load_secret(gmail.CLIENT_ID_ACCOUNT))
        self.gmail_id.setPlaceholderText("Google OAuth client id")
        self.gmail_secret = self._secret_field(gmail.CLIENT_SECRET_ACCOUNT, "Google OAuth client secret")
        self.gmail_button = QPushButton("Connect Gmail in your browser")
        self.gmail_button.clicked.connect(self._connect_gmail)
        self.gmail_status = QLabel("Gmail is connected." if gmail.is_connected() else "Gmail is not connected.")
        self.gmail_status.setObjectName("detail")
        self.gmail_status.setWordWrap(True)

        form = QFormLayout()
        form.addRow("Notion token", self.notion_token)
        form.addRow("Grok (xAI) key", self.grok_key)
        form.addRow("Gmail client id", self.gmail_id)
        form.addRow("Gmail client secret", self.gmail_secret)
        form.addRow("", self.gmail_button)
        form.addRow("", self.gmail_status)

        save = QPushButton("Save and close")
        save.clicked.connect(self._save)
        layout = QVBoxLayout(self)
        layout.addWidget(heading)
        layout.addWidget(note)
        layout.addLayout(form)
        layout.addWidget(save)
        self._gmail_done.connect(self._gmail_finished)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        dark_title_bar(self)

    @staticmethod
    def _secret_field(account: str, hint: str) -> QLineEdit:
        field = QLineEdit()
        field.setEchoMode(QLineEdit.EchoMode.Password)
        field.setPlaceholderText("Saved. Type here only to replace it." if keystore.load_secret(account) else hint)
        return field

    def _store_entries(self) -> None:
        for account, field in (
            (notion.TOKEN_ACCOUNT, self.notion_token),
            (grok.KEY_ACCOUNT, self.grok_key),
            (gmail.CLIENT_ID_ACCOUNT, self.gmail_id),
            (gmail.CLIENT_SECRET_ACCOUNT, self.gmail_secret),
        ):
            if field.text().strip():
                keystore.save_secret(account, field.text())

    def _connect_gmail(self) -> None:
        self._store_entries()
        client_id = keystore.load_secret(gmail.CLIENT_ID_ACCOUNT)
        client_secret = keystore.load_secret(gmail.CLIENT_SECRET_ACCOUNT)
        if not client_id or not client_secret:
            self.gmail_status.setText("Enter the client id and secret first.")
            return
        self.gmail_button.setEnabled(False)
        self.gmail_status.setText("Finish signing in with Google in your browser.")

        def work():
            try:
                gmail.connect(client_id, client_secret)
                self._gmail_done.emit("Gmail is connected.")
            except Exception as error:
                self._gmail_done.emit(f"Gmail did not connect: {error}")

        threading.Thread(target=work, name="alpha-gmail-connect", daemon=True).start()

    def _gmail_finished(self, message: str) -> None:
        self.gmail_button.setEnabled(True)
        self.gmail_status.setText(message)

    def _save(self) -> None:
        self._store_entries()
        self.accept()
