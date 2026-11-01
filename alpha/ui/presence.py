"""The animated presence: a calm core inside a ring of moving filaments.

`PresenceModel` holds all the motion and colour as plain numbers, so it can be
tested without drawing. `Presence` draws it, reusing cached images for the soft
glow, and stops its timer whenever it cannot be seen.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import QEvent, QLineF, QPointF, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap, QRadialGradient
from PySide6.QtWidgets import QWidget

from ..state import DEFAULT_COLOURS, State
from .style import BACKGROUND

EASE_SECONDS = 0.5
RESTING_INTERVAL_MS = 120
ACTIVE_INTERVAL_MS = 33
FILAMENTS = 72
GLOW_STEPS = 6
LAYER_REACH = 3.7


@dataclass(frozen=True)
class Motion:
    drift: float
    wave: float
    orbit: float
    ripple: float


MOTIONS = {
    State.IDLE: Motion(drift=0.28, wave=0.05, orbit=0.12, ripple=0.0),
    State.LISTENING: Motion(drift=0.5, wave=0.07, orbit=0.2, ripple=1.0),
    State.THINKING: Motion(drift=1.5, wave=0.1, orbit=1.1, ripple=0.0),
    State.SPEAKING: Motion(drift=0.8, wave=0.09, orbit=0.3, ripple=0.0),
    State.WARNING: Motion(drift=0.45, wave=0.08, orbit=0.2, ripple=0.0),
    State.ERROR: Motion(drift=0.2, wave=0.03, orbit=0.05, ripple=0.0),
}


def _rgb(hex_colour: str) -> tuple[float, float, float]:
    colour = QColor(hex_colour)
    return (float(colour.red()), float(colour.green()), float(colour.blue()))


def _ease(progress: float) -> float:
    return progress * progress * (3.0 - 2.0 * progress)


class PresenceModel:
    def __init__(self, colours: dict[str, str] | None = None):
        self._colours = dict(colours or DEFAULT_COLOURS)
        self.state = State.IDLE
        self.colour = _rgb(self._colours[self.state.value])
        self._from = self.colour
        self._to = self.colour
        self._progress = 1.0
        self.motion = MOTIONS[State.IDLE]
        self._motion_from = self.motion
        self.level = 0.0
        self._level_target = 0.0
        self.phase = 0.0
        self.orbit_angle = 0.0
        self.ripple_phase = 0.0

    @property
    def easing(self) -> bool:
        return self._progress < 1.0

    @property
    def lively(self) -> bool:
        return self.easing or self.state is not State.IDLE or self.level > 0.02

    def set_state(self, state: State) -> None:
        if state is self.state:
            return
        self.state = state
        self._aim()

    def set_colours(self, colours: dict[str, str]) -> None:
        self._colours = dict(colours)
        self._aim()

    def _aim(self) -> None:
        self._from = self.colour
        self._to = _rgb(self._colours[self.state.value])
        self._motion_from = self.motion
        self._progress = 0.0

    def set_level(self, level: float) -> None:
        self._level_target = min(1.0, max(0.0, level))

    def advance(self, seconds: float) -> None:
        if self._progress < 1.0:
            self._progress = min(1.0, self._progress + seconds / EASE_SECONDS)
            eased = _ease(self._progress)
            self.colour = tuple(a + (b - a) * eased for a, b in zip(self._from, self._to))
            goal = MOTIONS[self.state]
            old = self._motion_from
            self.motion = Motion(
                drift=old.drift + (goal.drift - old.drift) * eased,
                wave=old.wave + (goal.wave - old.wave) * eased,
                orbit=old.orbit + (goal.orbit - old.orbit) * eased,
                ripple=old.ripple + (goal.ripple - old.ripple) * eased,
            )
        # Rises fast and falls slowly, so speech looks alive rather than jittery.
        rate = 22.0 if self._level_target > self.level else 5.0
        self.level += (self._level_target - self.level) * min(1.0, rate * seconds)
        self.phase = (self.phase + seconds * self.motion.drift) % (math.tau * 64)
        self.orbit_angle = (self.orbit_angle + seconds * self.motion.orbit) % math.tau
        self.ripple_phase = (self.ripple_phase + seconds * 0.7) % 1.0

    def filament_lengths(self) -> np.ndarray:
        """How far each filament reaches, as a fraction of the ring's radius."""
        angles = np.linspace(0.0, math.tau, FILAMENTS, endpoint=False)
        shape = (
            np.sin(angles * 3 + self.phase)
            + 0.6 * np.sin(angles * 5 - self.phase * 1.4)
            + 0.4 * np.sin(angles * 11 + self.phase * 0.7)
        )
        return 0.1 + (self.motion.wave + self.level * 0.12) * (shape + 1.1)


class PresenceMotion:
    """The timing shared by both ways of drawing the presence.

    It only ticks while the presence can be seen, so a hidden or minimised
    window costs nothing.
    """

    resting_interval_ms = RESTING_INTERVAL_MS

    def _begin_motion(self, colours: dict[str, str] | None) -> None:
        self.model = PresenceModel(colours)
        self.setMinimumSize(320, 180)
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.CoarseTimer)
        self._timer.timeout.connect(self._tick)
        self._last_tick = 0.0
        self._level_source = lambda: 0.0
        self._watched_window: QWidget | None = None
        self.frames_drawn = 0

    @property
    def animating(self) -> bool:
        return self._timer.isActive()

    def set_level_source(self, source) -> None:
        self._level_source = source

    def set_voice_source(self, source) -> None:
        """Only the stage uses this."""

    def set_character(self, character_id: str) -> None:
        """Only the stage uses this."""

    def set_state(self, state: State) -> None:
        self.model.set_state(state)
        self._use_interval()

    def set_colours(self, colours: dict[str, str]) -> None:
        self.model.set_colours(colours)
        self._use_interval()

    def _use_interval(self) -> None:
        wanted = ACTIVE_INTERVAL_MS if self.model.lively else self.resting_interval_ms
        if self._timer.interval() != wanted:
            self._timer.setInterval(wanted)

    def _visible_on_screen(self) -> bool:
        window = self.window()
        return self.isVisible() and not window.isMinimized() and not window.isHidden()

    def _update_running(self) -> None:
        if self._visible_on_screen():
            if not self._timer.isActive():
                self._last_tick = time.monotonic()
                self._use_interval()
                self._timer.start()
        else:
            self._timer.stop()

    def showEvent(self, event) -> None:
        window = self.window()
        if window is not self._watched_window:
            if self._watched_window is not None:
                self._watched_window.removeEventFilter(self)
            window.installEventFilter(self)
            self._watched_window = window
        self._update_running()
        super().showEvent(event)

    def hideEvent(self, event) -> None:
        self._timer.stop()
        super().hideEvent(event)

    def eventFilter(self, watched, event) -> bool:
        if event.type() in (QEvent.Type.WindowStateChange, QEvent.Type.Hide, QEvent.Type.Show):
            self._update_running()
        return False

    def _tick(self) -> None:
        now = time.monotonic()
        seconds = min(0.1, now - self._last_tick)
        self._last_tick = now
        self.model.set_level(self._level_source())
        self.model.advance(seconds)
        self._use_interval()
        self._request_frame()


class Presence(PresenceMotion, QWidget):
    """Drawn on the processor. Used only when the graphics chip cannot do it."""

    def __init__(self, colours: dict[str, str] | None = None, parent: QWidget | None = None):
        QWidget.__init__(self, parent)
        self._begin_motion(colours)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self._layers: dict[int, QPixmap] = {}
        self._layer_key: tuple | None = None

    def _request_frame(self) -> None:
        self.update(self._drawn_area())

    def _radius(self) -> float:
        return min(self.width(), self.height()) * 0.29

    def _drawn_area(self) -> QRect:
        side = int(self._radius() * LAYER_REACH)
        return QRect((self.width() - side) // 2, (self.height() - side) // 2, side, side)

    def _layer(self, glow_step: int, side: int) -> QPixmap:
        """The opaque backdrop: dark background, glow and core, already blended.

        Blending a large soft glow is the slowest part of a frame. Making it once
        for each brightness step, then copying it, keeps idle drawing cheap.
        """
        colour = tuple(int(c) for c in self.model.colour)
        key = (colour, side)
        if key != self._layer_key:
            self._layer_key = key
            self._layers.clear()
        layer = self._layers.get(glow_step)
        if layer is not None:
            return layer

        tint = QColor(*colour)
        ratio = self.devicePixelRatioF()
        layer = QPixmap(int(side * ratio), int(side * ratio))
        layer.setDevicePixelRatio(ratio)
        layer.fill(QColor(BACKGROUND))
        painter = QPainter(layer)
        middle = side / 2
        radius = side / LAYER_REACH

        painter.setOpacity(0.62 + 0.38 * glow_step / (GLOW_STEPS - 1))
        glow = QRadialGradient(middle, middle, radius * 1.75)
        for stop, alpha in ((0.0, 120), (0.3, 70), (0.6, 22), (1.0, 0)):
            glow.setColorAt(stop, QColor(tint.red(), tint.green(), tint.blue(), alpha))
        painter.fillRect(0, 0, side, side, glow)

        painter.setOpacity(1.0)
        core = QRadialGradient(middle, middle, radius * 0.5)
        core.setColorAt(0.0, QColor(255, 255, 255, 245))
        core.setColorAt(0.25, tint.lighter(160))
        core.setColorAt(0.6, QColor(tint.red(), tint.green(), tint.blue(), 130))
        core.setColorAt(1.0, QColor(tint.red(), tint.green(), tint.blue(), 0))
        painter.fillRect(0, 0, side, side, core)
        painter.end()
        self._layers[glow_step] = layer
        return layer

    def paintEvent(self, event) -> None:
        model = self.model
        area = self._drawn_area()
        painter = QPainter(self)
        if not area.contains(event.rect()):
            painter.fillRect(event.rect(), QColor(BACKGROUND))

        breath = 0.5 + 0.5 * math.sin(model.phase * 0.9)
        strength = min(1.0, 0.3 * breath + 0.8 * model.level)
        layer = self._layer(round(strength * (GLOW_STEPS - 1)), area.width())
        painter.drawPixmap(area.topLeft(), layer)

        radius = self._radius()
        centre = QPointF(self.width() / 2, self.height() / 2)
        cx, cy = centre.x(), centre.y()
        tint = QColor(*(int(c) for c in model.colour))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        if model.motion.ripple > 0.01:
            for offset in (0.0, 0.5):
                travel = (model.ripple_phase + offset) % 1.0
                ring = radius * (1.0 + travel * 0.75)
                ripple = QColor(tint)
                ripple.setAlphaF(model.motion.ripple * (1.0 - travel) * 0.6)
                painter.setPen(QPen(ripple, 1.5))
                painter.drawEllipse(centre, ring, ring)

        angles = np.linspace(0.0, math.tau, FILAMENTS, endpoint=False) + model.phase * 0.08
        cos, sin = np.cos(angles).tolist(), np.sin(angles).tolist()
        lines = []
        for c, s, length in zip(cos, sin, model.filament_lengths().tolist()):
            outer = radius * (1.0 + length)
            lines.append(QLineF(cx + c * radius, cy + s * radius, cx + c * outer, cy + s * outer))
        filament = QColor(tint.lighter(135))
        filament.setAlphaF(0.85)
        painter.setPen(QPen(filament, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLines(lines)

        orbit = QColor(tint)
        orbit.setAlphaF(0.55)
        painter.setPen(QPen(orbit, 1.1))
        painter.translate(centre)
        for tilt, squash, direction in ((0.0, 0.34, 1.0), (1.05, 0.5, -1.0)):
            painter.save()
            painter.rotate(math.degrees(tilt + direction * model.orbit_angle))
            painter.drawEllipse(QRectF(-radius * 0.82, -radius * 0.82 * squash, radius * 1.64, radius * 1.64 * squash))
            painter.restore()
        painter.end()
        self.frames_drawn += 1

