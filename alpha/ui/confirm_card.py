"""The on-screen card where a person approves or refuses an action.

`ConfirmButton` is the only code that makes a `HumanPress`. It does so inside
its mouse handler and only for an event that came from the operating system,
so calling click() on it from code, however that code was reached, does nothing.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout

from ..actions import ActionGate, HumanPress, Proposal

ARM_DELAY_MS = 700
PREVIEW_CHARS = 2_000


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


class ConfirmCard(QFrame):
    confirmed = Signal(object)
    cancelled = Signal(str)

    def __init__(self, gate: ActionGate):
        super().__init__()
        self.setObjectName("card")
        self._gate = gate
        self._proposal: Proposal | None = None

        self._title = QLabel()
        self._title.setObjectName("cardTitle")
        self._title.setTextFormat(Qt.TextFormat.PlainText)
        self._where = QLabel()
        self._where.setTextFormat(Qt.TextFormat.PlainText)
        self._where.setWordWrap(True)
        self._preview = QPlainTextEdit()
        self._preview.setReadOnly(True)
        self._preview.setFixedHeight(64)
        self._countdown = QLabel()
        self._countdown.setObjectName("detail")
        self.confirm_button = ConfirmButton()
        self.confirm_button.human_pressed.connect(self.confirmed)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.cancel_button.clicked.connect(self._cancel)

        buttons = QHBoxLayout()
        buttons.addWidget(self._countdown, 1)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.confirm_button)
        layout = QVBoxLayout(self)
        layout.addWidget(self._title)
        layout.addWidget(self._where)
        layout.addWidget(self._preview)
        layout.addLayout(buttons)

        self._ticker = QTimer(self)
        self._ticker.setInterval(250)
        self._ticker.timeout.connect(self._show_time_left)
        self.hide()

    def present(self, proposal: Proposal) -> None:
        self._proposal = proposal
        self.confirm_button.proposal_id = proposal.id
        self.confirm_button.setEnabled(False)
        self._title.setText(f"APLHA is asking to {proposal.summary}")
        self._where.setText(proposal.where)
        self._preview.setPlainText(proposal.preview[:PREVIEW_CHARS])
        self._show_time_left()
        self._ticker.start()
        self.show()
        # A short pause, so a click already on its way cannot land on CONFIRM.
        QTimer.singleShot(ARM_DELAY_MS, self._arm)

    def _arm(self) -> None:
        if self._proposal is not None:
            self.confirm_button.setEnabled(True)

    def _show_time_left(self) -> None:
        if self._proposal is not None:
            self._countdown.setText(f"Expires in {self._gate.seconds_left(self._proposal):.0f} s")

    def _cancel(self) -> None:
        if self._proposal is not None:
            self.cancelled.emit(self._proposal.id)

    def dismiss(self, proposal: Proposal) -> None:
        if self._proposal is None or self._proposal.id != proposal.id:
            return
        self._proposal = None
        self.confirm_button.proposal_id = ""
        self._ticker.stop()
        self.hide()
