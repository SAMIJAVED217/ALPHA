"""Shows the QR code and the code that let a phone or tablet join."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QDialog, QLabel, QPushButton, QVBoxLayout

from .. import qr
from .style import dark_title_bar

SQUARE = 6
QUIET_SQUARES = 4


def picture_of(grid: list[list[bool]]) -> QPixmap:
    """A dark-on-white square with the clear border that phone cameras need."""
    side = (len(grid) + 2 * QUIET_SQUARES) * SQUARE
    image = QImage(side, side, QImage.Format.Format_RGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    for row, line in enumerate(grid):
        for column, dark in enumerate(line):
            if dark:
                painter.fillRect((column + QUIET_SQUARES) * SQUARE, (row + QUIET_SQUARES) * SQUARE, SQUARE, SQUARE, QColor("black"))
    painter.end()
    return QPixmap.fromImage(image)


class RemoteDialog(QDialog):
    def __init__(self, links: list[str], code: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Phone and tablet control")
        heading = QLabel("Scan with your phone or tablet")
        heading.setObjectName("heading")
        layout = QVBoxLayout(self)
        layout.addWidget(heading)
        if links:
            self.picture = QLabel()
            self.picture.setPixmap(picture_of(qr.make(f"{links[0]}/#{code}")))
            self.picture.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(self.picture)
        places = "\n".join(links) or "No network was found. Connect to Wi-Fi first."
        self.details = QLabel(
            f"Or open this address and enter the code:\n\n{places}\n\nCode: {code}\n\n"
            "It works once and lasts 5 minutes. Stay on the same Wi-Fi as this PC.\n"
            "The link is not encrypted, so use your own network.\n"
            "Actions that need CONFIRM still wait here on the PC."
        )
        self.details.setObjectName("detail")
        self.details.setWordWrap(True)
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.details)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        layout.addWidget(close)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        dark_title_bar(self)
