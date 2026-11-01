import gc
import threading

from PySide6.QtCore import QObject

from alpha.ui.tidy import InterfaceThreadTidier


def test_clean_up_only_runs_on_the_interface_thread(qapp):
    was_enabled = gc.isenabled()
    gc.enable()
    threads = []

    def note(phase, info):
        if phase == "start":
            threads.append(threading.current_thread())

    tidier = InterfaceThreadTidier()
    gc.callbacks.append(note)
    try:
        assert not gc.isenabled()

        def churn():
            for _ in range(200_000):
                item = []
                item.append(item)

        worker = threading.Thread(target=churn)
        worker.start()
        worker.join()
        assert worker not in threads

        tidier._sweep()
        assert threads == [threading.main_thread()]
    finally:
        gc.callbacks.remove(note)
        gc.unfreeze()
        tidier.deleteLater()
        if was_enabled:
            gc.enable()


def test_tidier_can_belong_to_a_window(qapp):
    parent = QObject()
    tidier = InterfaceThreadTidier(parent)
    assert tidier.parent() is parent
    gc.unfreeze()
