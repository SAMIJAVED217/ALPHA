from PySide6.QtWidgets import QApplication

from alpha.ui.single_instance import ShowRequests, wake_running_copy

from .test_interface import spin_until


def test_nobody_home_means_this_copy_should_start(qapp):
    assert wake_running_copy("alpha-test-nobody") is False


def test_a_second_launch_wakes_the_first(qapp):
    knocks = []
    listener = ShowRequests("alpha-test-knock", lambda: knocks.append(1))
    QApplication.processEvents()
    assert wake_running_copy("alpha-test-knock") is True
    assert spin_until(lambda: knocks == [1])
    listener.deleteLater()
