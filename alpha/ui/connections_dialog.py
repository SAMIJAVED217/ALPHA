"""Connect Notion, Gmail and Grok. Everything entered here goes to the Credential Manager."""

from __future__ import annotations

import logging
import threading
from concurrent.futures import Future

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout

from .. import keystore
from ..tools import gmail, grok, notion
from ..tools.registry import ToolRefused
from .style import dark_title_bar

log = logging.getLogger(__name__)
POLL_MS = 100


def _in_background(work, *arguments) -> Future:
    """The thread never touches the window. If it held the last reference to one, Qt would crash
    destroying that window off the interface thread."""
    future = Future()

    def run():
        try:
            future.set_result(work(*arguments))
        except Exception as error:
            future.set_exception(error)

    threading.Thread(target=run, name="alpha-connection", daemon=True).start()
    return future


class ConnectionsDialog(QDialog):
    def __init__(self, parent=None, notion_client=None, grok_client=None, groq_client=None):
        super().__init__(parent)
        self.setWindowTitle("APLHA connections")
        self.setMinimumWidth(520)
        self._notion = notion_client or notion.NotionClient()
        self._grok = grok_client or grok.GrokClient()
        self._groq = groq_client or grok.groq_client()

        heading = QLabel("Connections")
        heading.setObjectName("heading")
        note = QLabel("Secrets go to the Windows Credential Manager, never to a file.")
        note.setObjectName("detail")

        self.notion_token = self._secret_field(notion.TOKEN_ACCOUNT, "Paste your Notion integration token")
        self.notion_button, self.notion_status = self._connect_row(
            "Notion", notion.TOKEN_ACCOUNT, lambda: self._check("Notion", self.notion_button, self.notion_status, self._notion.check)
        )
        self.grok_key = self._secret_field(grok.KEY_ACCOUNT, "Paste your xAI API key")
        self.grok_button, self.grok_status = self._connect_row(
            "Grok", grok.KEY_ACCOUNT, lambda: self._check("Grok", self.grok_button, self.grok_status, self._grok.check)
        )
        self.groq_key = self._secret_field(grok.GROQ_KEY_ACCOUNT, "Paste your free Groq API key")
        self.groq_button, self.groq_status = self._connect_row(
            "Groq", grok.GROQ_KEY_ACCOUNT, lambda: self._check("Groq", self.groq_button, self.groq_status, self._groq.check)
        )
        self.keyword = self._secret_field(keystore.KEYWORD_ACCOUNT, "Common English words, known only to you")
        self.keyword_note = QLabel("Changing your details will need this spoken. It works after APLHA restarts.")
        self.keyword_note.setObjectName("detail")
        self.keyword_note.setWordWrap(True)
        self.gmail_id = QLineEdit(keystore.load_secret(gmail.CLIENT_ID_ACCOUNT))
        self.gmail_id.setPlaceholderText("Google OAuth client id")
        self.gmail_secret = self._secret_field(gmail.CLIENT_SECRET_ACCOUNT, "Google OAuth client secret")
        self.gmail_button = QPushButton("Connect Gmail in your browser")
        self.gmail_button.clicked.connect(self._connect_gmail)
        self.gmail_status = QLabel("Gmail is connected." if gmail.is_connected() else "Gmail is not connected.")
        self.gmail_status.setObjectName("detail")
        self.gmail_status.setWordWrap(True)

        form = QFormLayout()
        for name, field, button, status in (
            ("Notion token", self.notion_token, self.notion_button, self.notion_status),
            ("Groq key (free)", self.groq_key, self.groq_button, self.groq_status),
            ("Grok (xAI) key", self.grok_key, self.grok_button, self.grok_status),
        ):
            row = QHBoxLayout()
            row.addWidget(field, 1)
            row.addWidget(button)
            form.addRow(name, row)
            form.addRow("", status)
        form.addRow("Spoken keyword", self.keyword)
        form.addRow("", self.keyword_note)
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

    def showEvent(self, event) -> None:
        super().showEvent(event)
        dark_title_bar(self)

    @staticmethod
    def _secret_field(account: str, hint: str) -> QLineEdit:
        field = QLineEdit()
        field.setEchoMode(QLineEdit.EchoMode.Password)
        field.setPlaceholderText("Saved. Type here only to replace it." if keystore.load_secret(account) else hint)
        return field

    @staticmethod
    def _connect_row(name: str, account: str, on_click) -> tuple[QPushButton, QLabel]:
        button = QPushButton(f"Connect {name}")
        button.clicked.connect(on_click)
        saved = keystore.load_secret(account)
        status = QLabel(f"{name} has a saved key. Press Connect to test it." if saved else f"{name} is not connected.")
        status.setObjectName("detail")
        status.setWordWrap(True)
        return button, status

    def _store_entries(self) -> None:
        for account, field in (
            (notion.TOKEN_ACCOUNT, self.notion_token),
            (grok.KEY_ACCOUNT, self.grok_key),
            (grok.GROQ_KEY_ACCOUNT, self.groq_key),
            (keystore.KEYWORD_ACCOUNT, self.keyword),
            (gmail.CLIENT_ID_ACCOUNT, self.gmail_id),
            (gmail.CLIENT_SECRET_ACCOUNT, self.gmail_secret),
        ):
            if field.text().strip():
                keystore.save_secret(account, field.text().strip())

    def _check(self, name: str, button: QPushButton, status: QLabel, test) -> None:
        self._store_entries()
        button.setEnabled(False)
        status.setText(f"Checking {name}...")

        def finished(error: BaseException | None) -> None:
            button.setEnabled(True)
            if error is None:
                status.setText(f"{name} is connected.")
            elif isinstance(error, ToolRefused):
                status.setText(str(error))
            else:
                log.warning("%s could not be checked: %s", name, type(error).__name__)
                status.setText(f"{name} could not be reached ({type(error).__name__}).")

        self._when_done(_in_background(test), finished)

    def _when_done(self, future: Future, finished) -> None:
        timer = QTimer(self)
        timer.setInterval(POLL_MS)

        def poll():
            if future.done():
                timer.stop()
                timer.deleteLater()
                finished(future.exception())

        timer.timeout.connect(poll)
        timer.start()

    def _connect_gmail(self) -> None:
        self._store_entries()
        client_id = keystore.load_secret(gmail.CLIENT_ID_ACCOUNT)
        client_secret = keystore.load_secret(gmail.CLIENT_SECRET_ACCOUNT)
        if not client_id or not client_secret:
            self.gmail_status.setText("Enter the client id and secret first.")
            return
        self.gmail_button.setEnabled(False)
        self.gmail_status.setText("Finish signing in with Google in your browser.")

        def finished(error: BaseException | None) -> None:
            self.gmail_button.setEnabled(True)
            self.gmail_status.setText(f"Gmail did not connect: {error}" if error else "Gmail is connected.")

        self._when_done(_in_background(gmail.connect, client_id, client_secret), finished)

    def _save(self) -> None:
        self._store_entries()
        self.accept()
