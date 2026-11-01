"""The HUD's side panels. They show only real values, and a dash for anything unknown."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

UNKNOWN = "—"
_CORNER = 14


class Meter(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedHeight(4)
        self.value = 0.0
        self.tint = QColor("#2f6bff")

    def set_value(self, percent: float | None) -> None:
        self.value = 0.0 if percent is None else max(0.0, min(100.0, percent))
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#121a2b"))
        painter.fillRect(QRectF(0, 0, self.width() * self.value / 100, self.height()), self.tint)


class HudPanel(QFrame):
    def __init__(self, title: str):
        super().__init__()
        self.setObjectName("hudPanel")
        self.setFixedWidth(214)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self._tint = QColor("#2f6bff")
        self._values: dict[str, QLabel] = {}
        self._meters: dict[str, Meter] = {}
        heading = QLabel(title)
        heading.setObjectName("hudTitle")
        self._grid = QGridLayout()
        self._grid.setHorizontalSpacing(8)
        self._grid.setVerticalSpacing(3)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.addWidget(heading)
        layout.addSpacing(6)
        layout.addLayout(self._grid)
        layout.addStretch(1)

    def add_layout(self, layout) -> None:
        self._grid.addLayout(layout, self._grid.rowCount(), 0, 1, 2)

    def add_widget(self, widget) -> None:
        self._grid.addWidget(widget, self._grid.rowCount(), 0, 1, 2)

    def add_row(self, name: str, with_meter: bool = False) -> None:
        row = self._grid.rowCount()
        label = QLabel(name)
        label.setObjectName("hudName")
        value = QLabel(UNKNOWN)
        value.setObjectName("hudValue")
        value.setTextFormat(Qt.TextFormat.PlainText)
        value.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._grid.addWidget(label, row, 0)
        self._grid.addWidget(value, row, 1)
        self._values[name] = value
        if with_meter:
            meter = Meter()
            self._grid.addWidget(meter, row + 1, 0, 1, 2)
            self._meters[name] = meter
        self._grid.setRowMinimumHeight(self._grid.rowCount(), 8)

    def set_value(self, name: str, text: str, percent: float | None = None) -> None:
        if self._values[name].text() != text:
            self._values[name].setText(text)
        if name in self._meters:
            self._meters[name].set_value(percent)

    def value(self, name: str) -> str:
        return self._values[name].text()

    def set_tint(self, hex_colour: str) -> None:
        self._tint = QColor(hex_colour)
        for meter in self._meters.values():
            meter.tint = self._tint
            meter.update()
        self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        # A soft reflection across the top, and a lit edge, so the panel reads as thick glass.
        gloss = QLinearGradient(0, 0, 0, self.height() * 0.32)
        gloss.setColorAt(0.0, QColor(255, 255, 255, 34))
        gloss.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.fillRect(self.rect(), gloss)
        painter.setPen(QPen(QColor(255, 255, 255, 110), 1))
        painter.drawLine(1, 1, self.width() - 2, 1)
        painter.setPen(QPen(QColor(0, 0, 0, 120), 2))
        painter.drawLine(1, self.height() - 2, self.width() - 2, self.height() - 2)
        painter.setPen(QPen(self._tint, 3))
        right, bottom = self.width() - 1, self.height() - 1
        for x, y, across, down in ((0, 0, 1, 1), (right, 0, -1, 1), (0, bottom, 1, -1), (right, bottom, -1, -1)):
            painter.drawLine(x, y, x + across * _CORNER, y)
            painter.drawLine(x, y, x, y + down * _CORNER)
