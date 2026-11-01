"""The on-screen card where a person approves or refuses an action.

`ConfirmButton` makes a `HumanPress` inside its mouse handler and only for an
event that came from the operating system, so calling click() on it from code,
however that code was reached, does nothing.

Once the owner has a keyword, actions kept for them have no button at all. The
card asks for the keyword instead, spoken or typed in `KeywordBox`.
"""

from __future__ import annotations

import hmac

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from .. import keystore
from ..actions import EXPIRY_SECONDS, ActionGate, HumanPress, Proposal

ARM_DELAY_MS = 700
PREVIEW_CHARS = 2_000
CARD_WIDTH = 520
ASK_FOR_KEYWORD = "Type your keyword, then press Enter"
WRONG_KEYWORD = "That is not your keyword. Try again"


def same_keyword(typed: str, keyword: str) -> bool:
    """Compared as the listener hears it, so capitals and extra spaces do not matter."""
    typed, keyword = (" ".join(words.lower().split()).encode() for words in (typed, keyword))
    return bool(keyword) and hmac.compare_digest(typed, keyword)


class ConfirmButton(QPushButton):
    human_pressed = Signal(object)

    def __init__(self):
        super().__init__("CONFIRM")
        self.setObjectName("confirm")
        # No keyboard focus, so a stray Enter or Space cannot press it.
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.proposal_id = ""

    def mouseReleaseEvent(self, event) -> None:
        real_press = (
            self.isEnabled()
            and self.isDown()
            and event.button() == Qt.MouseButton.LeftButton
            and event.spontaneous()
            and self.rect().contains(event.position().toPoint())
        )
        super().mouseReleaseEvent(event)
        if real_press and self.proposal_id:
            self.human_pressed.emit(HumanPress(self.proposal_id))


class KeywordBox(QLineEdit):
    """Shows dots, and hands over what was typed only for a real Enter from the keyboard."""

    entered = Signal(str)

    def __init__(self):
        super().__init__()
        self.setObjectName("cardKeyword")
        self.setEchoMode(QLineEdit.EchoMode.Password)
        self.setPlaceholderText(ASK_FOR_KEYWORD)

    def keyPressEvent(self, event) -> None:
        if event.key() not in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            super().keyPressEvent(event)
        elif event.spontaneous():
            self.entered.emit(self.text())


class ConfirmCard(QFrame):
    confirmed = Signal(object)
    cancelled = Signal(str)

    def __init__(self, gate: ActionGate, load_keyword=keystore.owner_keyword):
        super().__init__()
        self.setObjectName("card")
        self.setFixedWidth(CARD_WIDTH)
        self._gate = gate
        self._load_keyword = load_keyword
        self._proposal: Proposal | None = None
        self._needs_keyword = False

        heading = QLabel("PERMISSION NEEDED")
        heading.setObjectName("cardHeading")
        self._title = QLabel()
        self._title.setObjectName("cardTitle")
        self._title.setTextFormat(Qt.TextFormat.PlainText)
        self._title.setWordWrap(True)
        self._where = QLabel()
        self._where.setObjectName("cardWhere")
        self._where.setTextFormat(Qt.TextFormat.PlainText)
        self._where.setWordWrap(True)
        self._preview = QPlainTextEdit()
        self._preview.setObjectName("cardPreview")
        self._preview.setReadOnly(True)
        self._preview.setFixedHeight(64)
        self._time_bar = QProgressBar()
        self._time_bar.setObjectName("cardTime")
        self._time_bar.setRange(0, 1000)
        self._time_bar.setTextVisible(False)
        self._time_bar.setFixedHeight(3)
        self._countdown = QLabel()
        self._countdown.setObjectName("cardCountdown")
        self.confirm_button = ConfirmButton()
        self.confirm_button.human_pressed.connect(self.confirmed)
        self.keyword_box = KeywordBox()
        self.keyword_box.entered.connect(self._keyword_entered)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("cardCancel")
        self.cancel_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.cancel_button.clicked.connect(self._cancel)

        buttons = QHBoxLayout()
        buttons.addWidget(self._countdown, 1)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.confirm_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 16)
        layout.setSpacing(10)
        layout.addWidget(heading)
        layout.addWidget(self._title)
        layout.addWidget(self._where)
        layout.addWidget(self._preview)
        layout.addWidget(self.keyword_box)
        layout.addSpacing(4)
        layout.addWidget(self._time_bar)
        layout.addLayout(buttons)

        self._ticker = QTimer(self)
        self._ticker.setInterval(250)
        self._ticker.timeout.connect(self._show_time_left)
        self.hide()

    def present(self, proposal: Proposal) -> None:
        self._proposal = proposal
        self._needs_keyword = proposal.owner_only and bool(self._load_keyword())
        self.confirm_button.proposal_id = "" if self._needs_keyword else proposal.id
        self.confirm_button.setEnabled(False)
        self.confirm_button.setVisible(not self._needs_keyword)
        self.keyword_box.clear()
        self.keyword_box.setPlaceholderText(ASK_FOR_KEYWORD)
        self.keyword_box.setVisible(self._needs_keyword)
        self._title.setText(f"Allow APLHA to {proposal.summary}?")
        self._where.setText(proposal.where)
        self._preview.setPlainText(proposal.preview[:PREVIEW_CHARS])
        self._preview.setVisible(bool(proposal.preview.strip()))
        self._show_time_left()
        self._ticker.start()
        self.show()
        if self._needs_keyword:
            self.keyword_box.setFocus()
        # A short pause, so a click already on its way cannot land on CONFIRM.
        QTimer.singleShot(ARM_DELAY_MS, self._arm)

    def _arm(self) -> None:
        if self._proposal is not None:
            self.confirm_button.setEnabled(True)

    def _keyword_entered(self, typed: str) -> None:
        if self._proposal is None or not self._needs_keyword:
            return
        if same_keyword(typed, self._load_keyword()):
            self.confirmed.emit(HumanPress(self._proposal.id))
        else:
            self.keyword_box.setPlaceholderText(WRONG_KEYWORD)
        self.keyword_box.clear()

    def _show_time_left(self) -> None:
        if self._proposal is not None:
            left = self._gate.seconds_left(self._proposal)
            words = "Say or type your keyword" if self._needs_keyword else "Say confirm"
            self._countdown.setText(f"{words}, or cancel. Expires in {left:.0f} s")
            self._time_bar.setValue(int(1000 * left / EXPIRY_SECONDS))

    def _cancel(self) -> None:
        if self._proposal is not None:
            self.cancelled.emit(self._proposal.id)

    def dismiss(self, proposal: Proposal) -> None:
        if self._proposal is None or self._proposal.id != proposal.id:
            return
        self._proposal = None
        self.confirm_button.proposal_id = ""
        self.keyword_box.clear()
        self._ticker.stop()
        self.hide()
