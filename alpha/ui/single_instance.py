"""Opening ALPHA while it is already running brings the running copy to the front."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject
from PySide6.QtNetwork import QLocalServer, QLocalSocket

_WAIT_MS = 500


def wake_running_copy(name: str) -> bool:
    socket = QLocalSocket()
    socket.connectToServer(name)
    found = socket.waitForConnected(_WAIT_MS)
    socket.disconnectFromServer()
    return found


class ShowRequests(QObject):
    """Listens on this computer only, and carries no data, just the fact of a knock."""

    def __init__(self, name: str, on_request: Callable[[], None], parent: QObject | None = None):
        super().__init__(parent)
        self._on_request = on_request
        QLocalServer.removeServer(name)
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._knocked)
        self._server.listen(name)

    def _knocked(self) -> None:
        while self._server.hasPendingConnections():
            self._server.nextPendingConnection().deleteLater()
            self._on_request()
