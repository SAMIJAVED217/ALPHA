"""The on-screen card where a person approves or refuses an action.

`ConfirmButton` is the only code that makes a `HumanPress`. It does so inside
its mouse handler and only for an event that came from the operating system,
so calling click() on it from code, however that code was reached, does nothing.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout

from ..actions import EXPIRY_SECONDS, ActionGate, HumanPress, Proposal

ARM_DELAY_MS = 700
PREVIEW_CHARS = 2_000
CARD_WIDTH = 520


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
        self.setFixedWidth(CARD_WIDTH)
        self._gate = gate
        self._proposal: Proposal | None = None

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
        layout.addSpacing(4)
        layout.addWidget(self._time_bar)
        layout.addLayout(buttons)

        self._ticker = QTimer(self)
        self._ticker.setInterval(250)
        self._ticker.timeout.connect(self._show_time_left)
        self.hide()

    def present(self, proposal: Proposal) -> None:
        self._proposal = proposal
        self.confirm_button.proposal_id = proposal.id
        self.confirm_button.setEnabled(False)
        self._title.setText(f"Allow APLHA to {proposal.summary}?")
        self._where.setText(proposal.where)
        self._preview.setPlainText(proposal.preview[:PREVIEW_CHARS])
        self._preview.setVisible(bool(proposal.preview.strip()))
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
            left = self._gate.seconds_left(self._proposal)
            self._countdown.setText(f"Say confirm or cancel. Expires in {left:.0f} s")
            self._time_bar.setValue(int(1000 * left / EXPIRY_SECONDS))

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
