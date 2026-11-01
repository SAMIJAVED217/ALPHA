"""Starts ALPHA."""

from __future__ import annotations

import ctypes
import faulthandler
import hashlib
import logging
import logging.handlers
import sys
import threading
from datetime import datetime

from . import paths, readiness, settings as settings_store

log = logging.getLogger("alpha")


def _start_logging() -> None:
    # Records a stack trace if native code kills the process, which logging cannot catch.
    faulthandler.enable(open(paths.crash_file(), "a", encoding="utf-8"), all_threads=True)
    handler = logging.handlers.RotatingFileHandler(
        paths.log_file(), maxBytes=1_000_000, backupCount=2, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.INFO)

    def report(kind, value, trace):
        log.critical("Unhandled error", exc_info=(kind, value, trace))

    sys.excepthook = report
    threading.excepthook = lambda args: report(args.exc_type, args.exc_value, args.exc_traceback)


def _instance_name() -> str:
    return "ALPHA-" + hashlib.sha256(str(paths.data_dir()).encode()).hexdigest()[:16]


def _say(text: str) -> None:
    ctypes.windll.user32.MessageBoxW(None, text, "APLHA", 0x40)


BRIEFING_DELAY_MS = 15_000


def run(connector=None, backend_factory=None, gate=None, load_key=None, title="APLHA") -> int:
    """The arguments exist for development runs and tests. Normal use passes none."""
    _start_logging()
    missing = readiness.missing()
    if missing:
        _say("APLHA is missing parts it needs: " + ", ".join(missing))
        return 1

    from PySide6.QtCore import QLockFile, QTimer
    from PySide6.QtWidgets import QApplication

    from .audio.hub import AudioHub
    from .keystore import load_key as saved_key
    from .tools.grok import GrokClient
    from .ui.main_window import MainWindow
    from .ui.single_instance import ShowRequests, wake_running_copy
    from .ui.style import SHEET
    from .ui.tidy import InterfaceThreadTidier
    from .voice.conversation import Conversation

    if sys.platform == "win32":
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ALPHA.Companion")

    app = QApplication(sys.argv[:1])
    app.setApplicationName("APLHA")
    app.setStyleSheet(SHEET)

    lock = QLockFile(str(paths.lock_file()))
    if not lock.tryLock(100):
        if not wake_running_copy(_instance_name()):
            _say("APLHA is already running.")
        return 0

    settings = settings_store.load()
    hub = AudioHub(backend_factory) if backend_factory else AudioHub()
    conversation = Conversation(
        settings, hub, gate=gate, connector=connector, load_key=load_key or saved_key,
        backup=None if connector else GrokClient(),
    )
    window = MainWindow(settings, hub, conversation)
    window.setWindowTitle(title)
    tidier = InterfaceThreadTidier(window)
    knocks = ShowRequests(_instance_name(), window.show_from_tray, window)
    conversation.start()
    window.showMaximized()
    if not settings.setup_complete:
        QTimer.singleShot(150, lambda: window.open_settings(first_run=True))
    elif connector is None:
        if settings.hands_free_at_start:
            conversation.set_hands_free(True)
        QTimer.singleShot(BRIEFING_DELAY_MS, lambda: window.maybe_brief(datetime.now()))

    code = app.exec()
    conversation.shutdown()
    hub.shutdown()
    lock.unlock()
    return code
