"""Isolation for every test run.

Before any ALPHA code loads, this file points ALPHA at a temporary folder, swaps
the Credential Manager for a throwaway in-memory store, and makes the sound
library impossible to import, so no test can reach a real device, key or setting.
A final check fails the run if the real settings file changed at all.
"""

from __future__ import annotations

import gc
import hashlib
import os
import sys
import tempfile
from pathlib import Path

import pytest

_home = tempfile.TemporaryDirectory(prefix="alpha-tests-")
os.environ["ALPHA_HOME"] = _home.name
os.environ["ALPHA_UNDER_TEST"] = "1"
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.modules["sounddevice"] = None

import keyring  # noqa: E402
from keyring.backend import KeyringBackend  # noqa: E402

from alpha import paths  # noqa: E402
from alpha.characters import CHARACTERS, Character  # noqa: E402

# Stand-ins, so tests of switching never depend on the owner's private characters.
for number, name in enumerate(("First", "Second", "Third", "Fourth")):
    CHARACTERS[name.lower()] = Character(
        name.lower(), name.upper(), "Test character", f"You are {name}.", ("Puck", "Kore", "Charon", "Aoede")[number], "David"
    )


class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self):
        self.saved: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):
        return self.saved.get((service, username))

    def set_password(self, service, username, password):
        self.saved[(service, username)] = password

    def delete_password(self, service, username):
        from keyring.errors import PasswordDeleteError

        if (service, username) not in self.saved:
            raise PasswordDeleteError("nothing saved")
        del self.saved[(service, username)]


_memory_keyring = MemoryKeyring()
keyring.set_keyring(_memory_keyring)


def _fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "absent"


@pytest.fixture(scope="session", autouse=True)
def real_settings_stay_untouched():
    real = paths.real_data_dir() / "settings.json"
    before = _fingerprint(real)
    yield
    assert _fingerprint(real) == before, "A test changed the real settings file."


@pytest.fixture(autouse=True)
def clean_home():
    """Each test starts with no settings, no key and an empty data folder."""
    _memory_keyring.saved.clear()
    home = Path(_home.name)
    for name in ("settings.json", "settings.json.partial"):
        (home / name).unlink(missing_ok=True)
    created = home / "Created files"
    if created.exists():
        for item in created.iterdir():
            item.unlink()
    yield
    # Same rule as the app: Qt objects are only ever cleaned up on the main thread.
    gc.collect()


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    gc.disable()
    return QApplication.instance() or QApplication([])
