"""Draws ALPHA's original icon: a bright core inside a ring of rays."""

from __future__ import annotations

import math
from pathlib import Path

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QRadialGradient

ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def draw(size: int) -> QImage:
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    centre = QPointF(size / 2, size / 2)

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#070b16"))
    corner = size * 0.2
    painter.drawRoundedRect(0, 0, size, size, corner, corner)

    glow = QRadialGradient(centre, size * 0.46)
    glow.setColorAt(0.0, QColor(47, 107, 255, 190))
    glow.setColorAt(0.6, QColor(47, 107, 255, 50))
    glow.setColorAt(1.0, QColor(47, 107, 255, 0))
    painter.setBrush(glow)
    painter.drawEllipse(centre, size * 0.46, size * 0.46)

    rays = 12 if size < 48 else 24
    inner = size * 0.27
    painter.setPen(QPen(QColor("#7fd6ff"), max(1.0, size / 42), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    for index in range(rays):
        angle = math.tau * index / rays
        reach = size * (0.38 if index % 2 == 0 or size < 48 else 0.33)
        painter.drawLine(
            QPointF(centre.x() + math.cos(angle) * inner, centre.y() + math.sin(angle) * inner),
            QPointF(centre.x() + math.cos(angle) * reach, centre.y() + math.sin(angle) * reach),
        )

    core = QRadialGradient(centre, size * 0.19)
    core.setColorAt(0.0, QColor("#ffffff"))
    core.setColorAt(0.45, QColor("#9fe6ff"))
    core.setColorAt(1.0, QColor(31, 162, 255, 0))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(core)
    painter.drawEllipse(centre, size * 0.19, size * 0.19)
    painter.end()
    return image


def write_ico(path: Path) -> None:
    """Writes a multi-size .ico. Each size is stored as a PNG, as Windows allows."""
    from PySide6.QtCore import QBuffer, QIODevice

    images = []
    for size in ICON_SIZES:
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        draw(size).save(buffer, "PNG")
        images.append((size, bytes(buffer.data())))

    header = (0).to_bytes(2, "little") + (1).to_bytes(2, "little") + len(images).to_bytes(2, "little")
    offset = 6 + 16 * len(images)
    entries = b""
    for size, data in images:
        side = 0 if size == 256 else size
        entries += bytes((side, side, 0, 0))
        entries += (1).to_bytes(2, "little") + (32).to_bytes(2, "little")
        entries += len(data).to_bytes(4, "little") + offset.to_bytes(4, "little")
        offset += len(data)
    path.write_bytes(header + entries + b"".join(data for _, data in images))
