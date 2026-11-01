"""Keeps Python's memory clean-up on the interface thread.

Clean-up normally runs on whichever thread happens to be busy. If that is a
background thread and it finds a dead Qt window object, Qt aborts the whole
process, because such objects may only be destroyed on the interface thread.
"""

from __future__ import annotations

import gc

from PySide6.QtCore import QObject, QTimer

_QUICK_SWEEP_MS = 5_000
_QUICK_SWEEPS_PER_FULL_SWEEP = 12


class InterfaceThreadTidier(QObject):
    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._sweeps = 0
        # Everything alive after start-up stays alive, so full sweeps can skip it.
        gc.collect()
        gc.freeze()
        gc.disable()
        self._timer = QTimer(self)
        self._timer.setInterval(_QUICK_SWEEP_MS)
        self._timer.timeout.connect(self._sweep)
        self._timer.start()

    def _sweep(self) -> None:
        self._sweeps += 1
        full = self._sweeps % _QUICK_SWEEPS_PER_FULL_SWEEP == 0
        gc.collect(2 if full else 1)
