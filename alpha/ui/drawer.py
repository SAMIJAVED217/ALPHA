"""A side panel that stays out of the way until its arrow is clicked."""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRect, Qt, Signal
from PySide6.QtWidgets import QPushButton, QWidget

ARROW_SIZE = (26, 84)
GAP = 4
SLIDE_MS = 220


class Drawer(QWidget):
    toggled = Signal(bool)

    def __init__(self, side: str, width: int, parent: QWidget):
        super().__init__(parent)
        self.side = side
        self.is_open = False
        self.setFixedWidth(width)
        self._area = QRect(0, 0, width, 0)
        self._arrow_y = 0
        self._motion = QPropertyAnimation(self, b"pos", self)
        self._motion.setDuration(SLIDE_MS)
        self._motion.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._motion.finished.connect(self._settle)
        self.arrow = QPushButton(parent)
        self.arrow.setObjectName("drawerArrow")
        self.arrow.setFixedSize(*ARROW_SIZE)
        self.arrow.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.arrow.clicked.connect(lambda: self.set_open(not self.is_open))
        self._label_arrow()
        self.hide()

    def place(self, area: QRect, arrow_middle: int) -> None:
        """Where the drawer sits when open. Called whenever the window changes size."""
        self._motion.stop()
        self._area = area
        self._arrow_y = arrow_middle - ARROW_SIZE[1] // 2
        self.setFixedHeight(area.height())
        self.move(self._spot(self.is_open))
        self._settle()
        self._seat_arrow()

    def set_open(self, wanted: bool) -> None:
        if wanted == self.is_open:
            return
        self.is_open = wanted
        self._label_arrow()
        self.show()
        self.raise_()
        self.arrow.raise_()
        self._motion.stop()
        self._motion.setStartValue(self.pos())
        self._motion.setEndValue(self._spot(wanted))
        self._motion.start()
        self.toggled.emit(wanted)

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        self._seat_arrow()

    def _spot(self, opened: bool) -> QPoint:
        if self.side == "left":
            x = self._area.left() if opened else -self.width() - ARROW_SIZE[0]
        elif opened:
            x = self._area.right() - self.width() + 1
        else:
            x = self.parentWidget().width() + ARROW_SIZE[0]
        return QPoint(x, self._area.top())

    def _seat_arrow(self) -> None:
        if self.side == "left":
            x = max(0, self.x() + self.width() + GAP)
        else:
            x = min(self.parentWidget().width() - ARROW_SIZE[0], self.x() - ARROW_SIZE[0] - GAP)
        self.arrow.move(x, self._arrow_y)

    def _settle(self) -> None:
        self.setVisible(self.is_open)
        self._seat_arrow()

    def _label_arrow(self) -> None:
        points_out = (self.side == "left") != self.is_open
        self.arrow.setText("❯" if points_out else "❮")
