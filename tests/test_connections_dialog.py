import gc

import httpx
import pytest
from PySide6.QtCore import QCoreApplication, QEvent

from alpha import keystore
from alpha.tools import grok, notion
from alpha.tools.notion import NotionClient
from alpha.tools.registry import ToolRefused
from alpha.ui.connections_dialog import ConnectionsDialog

from .test_interface import spin_until


class Check:
    def __init__(self, error=None):
        self.error = error
        self.calls = 0

    def check(self):
        self.calls += 1
        if self.error:
            raise self.error


opened = []


@pytest.fixture(autouse=True)
def closed_on_this_thread(qapp):
    """A window left for the end of the run is destroyed after Qt has gone, which crashes the process."""
    yield
    while opened:
        opened.pop().deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()


def dialog(qapp, notion_check=None, grok_check=None):
    opened.append(
        ConnectionsDialog(notion_client=notion_check or Check(), grok_client=grok_check or Check(), groq_client=Check())
    )
    return opened[-1]


def test_connect_saves_a_pasted_groq_key_and_says_it_worked(qapp):
    made = dialog(qapp)
    made.groq_key.setText("gsk-key")
    made.groq_button.click()
    assert spin_until(lambda: made.groq_status.text() == "Groq is connected.")
    assert keystore.load_secret(grok.GROQ_KEY_ACCOUNT) == "gsk-key"


def test_connect_saves_a_pasted_notion_token_tidily_and_says_it_worked(qapp):
    made = dialog(qapp)
    made.notion_token.setText("  a-token  ")
    made.notion_button.click()
    assert spin_until(lambda: made.notion_status.text() == "Notion is connected.")
    assert keystore.load_secret(notion.TOKEN_ACCOUNT) == "a-token"
    assert made.notion_button.isEnabled()


def test_connect_saves_a_pasted_grok_key_and_says_it_worked(qapp):
    made = dialog(qapp)
    made.grok_key.setText("a-key")
    made.grok_button.click()
    assert spin_until(lambda: made.grok_status.text() == "Grok is connected.")
    assert keystore.load_secret(grok.KEY_ACCOUNT) == "a-key"


def test_a_refused_key_shows_the_reason(qapp):
    made = dialog(qapp, grok_check=Check(ToolRefused("xAI refused the key.")))
    made.grok_key.setText("a-key")
    made.grok_button.click()
    assert spin_until(lambda: made.grok_status.text() == "xAI refused the key.")
    assert made.grok_button.isEnabled()


def test_an_unexpected_problem_never_shows_its_own_text(qapp):
    made = dialog(qapp, notion_check=Check(RuntimeError("secret-looking detail")))
    made.notion_button.click()
    assert spin_until(lambda: "could not be reached" in made.notion_status.text())
    assert "secret-looking" not in made.notion_status.text()


def test_the_window_shows_a_saved_key_without_claiming_it_works(qapp):
    keystore.save_secret(notion.TOKEN_ACCOUNT, "a-token")
    assert "Press Connect" in dialog(qapp).notion_status.text()


def test_the_window_fits_a_small_laptop_screen(qapp):
    assert dialog(qapp).sizeHint().height() <= 640


def test_checking_notion_uses_a_harmless_request():
    seen = []

    def reply(request):
        seen.append(request)
        return httpx.Response(200, json={"object": "user"})

    NotionClient(lambda: "t", httpx.MockTransport(reply)).check()
    assert [(request.method, request.url.path) for request in seen] == [("GET", "/v1/users/me")]
