"""Small HUD parts: ring gauges, the character switcher, the voice waveform and 3D buttons."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QConicalGradient,
    QFont,
    QLinearGradient,
    QPainter,
    QPen,
    QPixmap,
    QRadialGradient,
    QTransform,
)
from PySide6.QtWidgets import QAbstractButton, QFrame, QLabel, QScrollArea, QVBoxLayout, QWidget

from ..characters import CHARACTERS, Character
from .stage import portrait_for


class RingGauge(QWidget):
    def __init__(self, label: str, side: int = 62):
        super().__init__()
        self.setFixedSize(side, side + 16)
        self.label = label
        self.percent: float | None = None
        self.tint = QColor("#2f6bff")

    def set_value(self, percent: float | None) -> None:
        self.percent = percent
        self.update()

    def set_tint(self, colour: QColor) -> None:
        self.tint = colour
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = self.width()
        box = QRectF(4, 4, side - 8, side - 8)
        painter.setPen(QPen(QColor(120, 150, 220, 70), 6))
        painter.drawEllipse(box)
        sweep = QConicalGradient(QPointF(side / 2, side / 2), 90)
        sweep.setColorAt(0.0, QColor("#22e6ff"))
        sweep.setColorAt(1.0, QColor("#a45bff"))
        painter.setPen(QPen(QBrush(sweep), 6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        if self.percent is not None:
            painter.drawArc(box, 90 * 16, int(-360 * 16 * self.percent / 100))
        painter.setPen(QColor("#ffffff"))
        painter.setFont(QFont("Consolas", 11, QFont.Weight.Bold))
        text = "—" if self.percent is None else f"{self.percent:.0f}%"
        painter.drawText(QRectF(0, 0, side, side), Qt.AlignmentFlag.AlignCenter, text)
        painter.setPen(QColor("#b9c8ea"))
        painter.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
        painter.drawText(QRectF(0, side, side, 16), Qt.AlignmentFlag.AlignCenter, self.label)


class CharacterTile(QAbstractButton):
    def __init__(self, character: Character):
        super().__init__()
        self.character = character
        self.setCheckable(True)
        self.setFixedHeight(76)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.tint = QColor("#2f6bff")
        self._portrait: QPixmap | None = None
        self.refresh()

    def refresh(self) -> None:
        picture = portrait_for(self.character.id)
        square = min(picture.width(), picture.height() // 2)
        crop = picture.copy((picture.width() - square) // 2, 0, square, square)
        self._portrait = QPixmap.fromImage(crop.scaled(
            QSize(76, 76), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation,
        ))
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(170, 92)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        area = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        painter.setBrush(QColor(120, 60, 220, 170) if self.isChecked() else QColor(30, 44, 110, 170))
        painter.setPen(QPen(QColor("#22e6ff") if self.isChecked() else QColor(120, 170, 255, 110), 2))
        painter.drawRoundedRect(area, 4, 4)
        painter.drawPixmap(8, 8, self._portrait)
        painter.setPen(QColor("#f2f6ff"))
        painter.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        painter.drawText(QRectF(90, 22, 80, 22), Qt.AlignmentFlag.AlignLeft, self.character.name)
        painter.setPen(QColor("#d3def5"))
        painter.setFont(QFont("Segoe UI", 8))
        painter.drawText(QRectF(90, 46, 80, 32), Qt.TextFlag.TextWordWrap, self.character.title)


class CharacterSwitch(QFrame):
    chosen = Signal(str)

    def __init__(self, selected: str):
        super().__init__()
        self.setObjectName("glass")
        self.setFixedWidth(200)
        self.tiles: dict[str, CharacterTile] = {}
        heading = QLabel("CHARACTERS")
        heading.setObjectName("hudTitle")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)
        layout.addWidget(heading)
        holder = QWidget()
        holder.setObjectName("characterHolder")
        tiles = QVBoxLayout(holder)
        tiles.setContentsMargins(0, 0, 0, 0)
        tiles.setSpacing(6)
        for character in CHARACTERS.values():
            tile = CharacterTile(character)
            tile.clicked.connect(lambda _on=False, name=character.id: self.choose(name))
            self.tiles[character.id] = tile
            tiles.addWidget(tile)
        tiles.addStretch(1)
        self.scroller = QScrollArea()
        self.scroller.setObjectName("characterList")
        self.scroller.setWidget(holder)
        self.scroller.setWidgetResizable(True)
        self.scroller.setFrameShape(QFrame.Shape.NoFrame)
        self.scroller.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(self.scroller, 1)
        self.show_selected(selected)

    def choose(self, character_id: str) -> None:
        self.show_selected(character_id)
        self.chosen.emit(character_id)

    def show_selected(self, character_id: str) -> None:
        for name, tile in self.tiles.items():
            tile.setChecked(name == character_id)

    def set_tint(self, colour: QColor) -> None:
        for tile in self.tiles.values():
            tile.tint = colour
            tile.update()


class Waveform(QWidget):
    """Bars driven by the true sound level. Flat means silence."""

    BARS = 28

    def __init__(self):
        super().__init__()
        self.setFixedHeight(46)
        self.setMinimumWidth(140)
        self.level = 0.0
        self.phase = 0.0
        self.tint = QColor("#2f6bff")

    def set_level(self, level: float) -> None:
        self.level = level
        self.phase += 0.7
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(self.tint, 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        middle = self.height() / 2
        step = self.width() / self.BARS
        for index in range(self.BARS):
            shape = abs(math.sin(index * 0.9 + self.phase)) * (1.0 - abs(index / self.BARS - 0.5) * 1.5)
            reach = 1.5 + self.level * shape * middle * 1.6
            x = step * (index + 0.5)
            painter.drawLine(int(x), int(middle - reach), int(x), int(middle + reach))


class HoloButton(QAbstractButton):
    """A thick glass capsule. It leans towards the pointer and sinks when pressed."""

    SIZE = QSize(150, 50)
    MOST_TILT = 20.0
    DEPTH = 4.0
    SINK = 3.0
    RESTING = 0.45
    NOTICED = 1.0

    def __init__(self, text: str):
        super().__init__()
        self.setText(text)
        self.setFixedSize(self.SIZE)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.tint = QColor("#22e6ff")
        self.tilt = 0.0
        self._pointer: QPointF | None = None

    def set_tint(self, colour: QColor) -> None:
        self.tint = colour
        self.update()

    def mouseMoveEvent(self, event) -> None:
        self._pointer = event.position()
        self.tilt = max(-1.0, min(1.0, self._pointer.x() / self.width() * 2.0 - 1.0))
        self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        self._pointer = None
        self.tilt = 0.0
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setOpacity(self.NOTICED if self._pointer is not None else self.RESTING)
        middle = QPointF(self.width() / 2, self.height() / 2)
        lean = QTransform().translate(middle.x(), middle.y())
        lean.rotate(self.tilt * self.MOST_TILT, Qt.Axis.YAxis)
        lean.translate(-middle.x(), -middle.y())
        painter.setTransform(lean)
        sink = self.SINK if self.isDown() else 0.0
        face = QRectF(10, 6 + sink, self.width() - 20, self.height() - 18)
        radius = face.height() / 2
        self._paint_glow(painter, face, radius)
        self._paint_side(painter, face, radius, self.DEPTH - sink)
        self._paint_face(painter, face, radius)
        self._paint_text(painter, face)

    def _paint_glow(self, painter: QPainter, face: QRectF, radius: float) -> None:
        strength = 1.6 if self._pointer is not None else 1.0
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for spread, alpha in ((7, 22), (4, 38), (2, 60)):
            glow = QColor(self.tint)
            glow.setAlpha(int(alpha * strength))
            painter.setPen(QPen(glow, spread))
            painter.drawRoundedRect(face, radius, radius)

    def _paint_side(self, painter: QPainter, face: QRectF, radius: float, depth: float) -> None:
        wall = QLinearGradient(0, face.bottom() - radius, 0, face.bottom() + depth)
        near, far = self.tint.darker(170), self.tint.darker(420)
        near.setAlpha(110)
        far.setAlpha(70)
        wall.setColorAt(0.0, near)
        wall.setColorAt(1.0, far)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(wall)
        painter.drawRoundedRect(face.translated(0, depth), radius, radius)

    def _paint_face(self, painter: QPainter, face: QRectF, radius: float) -> None:
        glass = QLinearGradient(0, face.top(), 0, face.bottom())
        glass.setColorAt(0.0, QColor(90, 120, 200, 70))
        glass.setColorAt(0.55, QColor(14, 22, 62, 60))
        glass.setColorAt(1.0, QColor(24, 18, 72, 70))
        painter.setBrush(glass)
        rim = QLinearGradient(0, face.top(), 0, face.bottom())
        rim.setColorAt(0.0, QColor(255, 255, 255, 235))
        rim.setColorAt(0.5, self.tint)
        rim.setColorAt(1.0, self.tint.darker(150))
        painter.setPen(QPen(QBrush(rim), 1.6))
        painter.drawRoundedRect(face, radius, radius)
        spot = self._pointer or QPointF(face.left() + face.width() * 0.3, face.top())
        shine = QColor(self.tint).lighter(150)
        shine.setAlpha(150 if self._pointer is not None else 70)
        light = QRadialGradient(spot, face.width() * 0.42)
        light.setColorAt(0.0, shine)
        light.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(light)
        painter.drawRoundedRect(face, radius, radius)
        gloss = QLinearGradient(0, face.top(), 0, face.center().y())
        gloss.setColorAt(0.0, QColor(255, 255, 255, 80))
        gloss.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setBrush(gloss)
        painter.drawRoundedRect(face.adjusted(6, 2, -6, -face.height() / 2), radius / 2, radius / 2)

    def _paint_text(self, painter: QPainter, face: QRectF) -> None:
        font = QFont("Segoe UI", 9, QFont.Weight.DemiBold)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 3)
        painter.setFont(font)
        painter.setPen(QColor(0, 0, 0, 170))
        painter.drawText(face.translated(0, 1.5), Qt.AlignmentFlag.AlignCenter, self.text())
        painter.setPen(QColor("#ffffff"))
        painter.drawText(face, Qt.AlignmentFlag.AlignCenter, self.text())
